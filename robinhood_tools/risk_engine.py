"""Deterministic risk engine facade (Phase 2 PR 2).

One `RiskEngine.check()` entry point over the existing rule implementations
in `risk.py`, `safety.py`, and `universe.py`. Every rule delegates to the
same functions the trading paths call today, and every rule keeps its
original test — with two intentional hardenings over the live equity path,
documented below: (a) a `MIN_CONFIDENCE` gate (default 75, constructor- or
config-owned) with missing confidence failing closed; (b) the news gate
also binds sells. The engine is pure deterministic code: no LLM, no
network, no broker. Internal errors also FAIL (never crash through a gate).

Fail-closed contract: any missing evidence that the live path requires is
a FAIL, not a skip. SKIP is recorded only where the underlying path also
has no check (sell-side buy-only rules).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from .errors import PolicyViolation
from .risk import PortfolioState, RiskLimits
from .safety import DataQuality, MarketRegime, require_earnings_clear
from .universe import Sp500Snapshot

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class TradeProposal:
    symbol: str
    side: Literal["buy", "sell"]
    order_type: str = "limit"
    time_in_force: str = "gfd"
    quantity: Decimal | None = None
    limit_price: Decimal | None = None
    intended_price: Decimal | None = None
    extended_hours: bool = False
    universe_snapshot: Sp500Snapshot | None = None
    earnings_date: date | None = None  # None fails closed (unknown = block)
    spread_pct: Decimal | None = None
    avg_daily_dollar_volume: Decimal | None = None
    order_value: Decimal | None = None
    portfolio: PortfolioState | None = None
    sector: str = ""
    score: int | None = None
    market_regime: MarketRegime | None = None
    confidence: int | None = None
    data_quality: DataQuality | None = None
    material_negative_news: bool = False


@dataclass(frozen=True)
class RuleResult:
    name: str
    passed: bool
    skipped: bool = False
    reason: str = ""


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    results: tuple[RuleResult, ...] = field(default_factory=tuple)

    @property
    def failed(self) -> list[RuleResult]:
        return [r for r in self.results if not r.passed and not r.skipped]

    def raise_on_fail(self, proposal: TradeProposal | None = None) -> None:
        if self.approved:
            return
        detail = "; ".join(f"{r.name}: {r.reason}" for r in self.failed)
        context = f" [{proposal.side} {proposal.symbol}]" if proposal else ""
        raise PolicyViolation(f"Risk engine rejected the trade{context} ({detail}).")


class RiskEngine:
    """Facade over existing validators. Mirrors live-path flag semantics."""

    def __init__(self, *, risk_limits: RiskLimits, earnings_blackout_days: int = 5,
                 min_confidence: int = 75, earliest_entry_et: str = "10:15",
                 latest_entry_et: str = "15:30",
                 max_order_value: Decimal | None = None,
                 mode: str = "paper_auto", paper_broker: str = "alpaca",
                 paper_trading_enabled: bool = True, paper_autonomy_enabled: bool = True,
                 human_approval_required: bool = False, regular_session_only: bool = True,
                 require_limit_orders: bool = True, forbid_price_chasing: bool = True,
                 entry_minimum_scores: dict | None = None,
                 live_trading_enabled: bool = False):
        self.risk_limits = risk_limits
        self.earnings_blackout_days = earnings_blackout_days
        self.min_confidence = min_confidence
        self.earliest_entry_et = earliest_entry_et
        self.latest_entry_et = latest_entry_et
        self.max_order_value = max_order_value if max_order_value is not None else risk_limits.max_order_value
        self.mode = mode
        self.paper_broker = paper_broker
        self.paper_trading_enabled = paper_trading_enabled
        self.paper_autonomy_enabled = paper_autonomy_enabled
        self.human_approval_required = human_approval_required
        self.regular_session_only = regular_session_only
        self.require_limit_orders = require_limit_orders
        self.forbid_price_chasing = forbid_price_chasing
        self.entry_minimum_scores = entry_minimum_scores or {"risk_on": 90, "neutral": 93, "risk_off": 97}
        self.live_trading_enabled = live_trading_enabled

    @classmethod
    def paper_from_settings(cls, settings) -> RiskEngine:
        return cls(
            risk_limits=settings.risk_limits,
            earnings_blackout_days=settings.earnings_blackout_days,
            earliest_entry_et=settings.paper_autonomy.earliest_entry_time_et,
            latest_entry_et=settings.paper_autonomy.latest_entry_time_et,
            max_order_value=settings.risk_limits.max_order_value,
            mode=settings.mode,
            paper_broker=settings.paper_broker,
            paper_trading_enabled=settings.paper_trading_enabled,
            paper_autonomy_enabled=settings.paper_autonomy.enabled,
            human_approval_required=settings.paper_autonomy.human_approval_required,
            regular_session_only=settings.paper_autonomy.regular_session_only,
            require_limit_orders=settings.paper_autonomy.require_limit_orders,
            forbid_price_chasing=settings.paper_autonomy.forbid_price_chasing,
            entry_minimum_scores=dict(settings.entry_minimum_scores),
            live_trading_enabled=settings.trading_enabled,
        )

    def check(self, proposal: TradeProposal, *, today: date | None = None,
              now_et: datetime | None = None, market_open: bool | None = None,
              database=None) -> RiskDecision:
        today = today or date.today()
        results: list[RuleResult] = []

        def run(name: str, func) -> None:
            try:
                outcome = func()
            except PolicyViolation as exc:
                results.append(RuleResult(name, False, reason=str(exc)))
                return
            except Exception as exc:  # noqa: BLE001 - gates fail closed, never crash through
                results.append(RuleResult(name, False,
                                          reason=f"internal error ({type(exc).__name__}: "
                                                 f"{str(exc)[:200]}); failing closed"))
                return
            if outcome == "skip":
                results.append(RuleResult(name, True, skipped=True, reason="not applicable to this side"))
            else:
                results.append(RuleResult(name, True))

        run("mode", lambda: self._check_mode())
        run("session", lambda: self._check_session(now_et, market_open))
        run("universe", lambda: self._check_universe(proposal))
        run("earnings", lambda: self._check_earnings(proposal, today))
        run("news", lambda: self._check_news(proposal))
        run("data_quality", lambda: self._check_data_quality(proposal))
        run("regime", lambda: self._check_regime(proposal))
        run("sizing", lambda: self._check_sizing(proposal))
        run("shape", lambda: self._check_shape(proposal))
        run("confidence", lambda: self._check_confidence(proposal))
        run("state", lambda: self._check_state(proposal, today, database))

        approved = all(r.passed for r in results)
        return RiskDecision(approved, tuple(results))

    # -- rules (each mirrors the live-path validator it names) ---------------
    def _check_mode(self) -> None:
        if self.live_trading_enabled:
            raise PolicyViolation("Live kill switch is open; autonomous paper path refuses.")
        if self.mode != "paper_auto" or self.paper_broker != "alpaca":
            raise PolicyViolation("Paper execution requires paper_auto mode with the Alpaca broker.")
        if not self.paper_trading_enabled or not self.paper_autonomy_enabled:
            raise PolicyViolation("Autonomous paper execution is disabled.")
        if self.human_approval_required:
            raise PolicyViolation("Paper autonomy cannot run while human approval is required.")

    def _check_session(self, now_et: datetime | None, market_open: bool | None) -> None:
        if now_et is None:
            raise PolicyViolation("No session timestamp; cannot verify the trading window.")
        if now_et.tzinfo is None:
            raise PolicyViolation("Session timestamp lacks a timezone; refusing.")
        try:
            earliest = datetime.strptime(self.earliest_entry_et, "%H:%M").time()
            latest = datetime.strptime(self.latest_entry_et, "%H:%M").time()
        except ValueError as exc:
            raise PolicyViolation("Entry window is misconfigured.") from exc
        local = now_et.astimezone(ET)
        if self.regular_session_only:
            if local.weekday() >= 5:
                raise PolicyViolation("Weekend; no autonomous entries.")
            if not earliest <= local.time().replace(tzinfo=None) <= latest:
                raise PolicyViolation(
                    f"Outside {self.earliest_entry_et}-{self.latest_entry_et} ET window.")
            if market_open is not True:
                raise PolicyViolation("Market clock is not confirmed open.")

    def _check_universe(self, proposal: TradeProposal):
        if proposal.side != "buy":
            return "skip"
        if proposal.universe_snapshot is None:
            raise PolicyViolation("No S&P 500 membership evidence; buys blocked.")
        proposal.universe_snapshot.require_current_member(proposal.symbol)

    def _check_earnings(self, proposal: TradeProposal, today: date):
        if proposal.side != "buy":
            return "skip"
        require_earnings_clear(today=today, earnings_date=proposal.earnings_date,
                               blackout_days=self.earnings_blackout_days)

    def _check_news(self, proposal: TradeProposal) -> None:
        if proposal.material_negative_news:
            raise PolicyViolation("Material negative news blocks autonomous entry.")

    def _check_data_quality(self, proposal: TradeProposal):
        if proposal.side != "buy":
            return "skip"
        if proposal.data_quality is None:
            raise PolicyViolation("No data-quality evidence; buys blocked.")
        proposal.data_quality.require_complete()

    def _check_regime(self, proposal: TradeProposal):
        if proposal.side != "buy":
            return "skip"
        if proposal.score is None or proposal.market_regime is None:
            raise PolicyViolation("No score/regime evidence; buys blocked.")
        try:
            minimum = self.entry_minimum_scores[proposal.market_regime]
        except KeyError as exc:
            raise PolicyViolation(f"Unknown market regime {proposal.market_regime!r}.") from exc
        if proposal.score < minimum:
            raise PolicyViolation(f"{proposal.market_regime} regime requires score >= {minimum}.")

    def _check_sizing(self, proposal: TradeProposal):
        if proposal.side != "buy":
            return "skip"
        if proposal.order_value is not None and proposal.order_value > self.max_order_value:
            raise PolicyViolation(f"Order value ${proposal.order_value} exceeds ${self.max_order_value} cap.")
        if proposal.spread_pct is not None and proposal.spread_pct > self.risk_limits.max_spread_pct:
            raise PolicyViolation(f"Spread {proposal.spread_pct:.3%} exceeds limit.")
        if (proposal.portfolio is None or proposal.order_value is None
                or proposal.avg_daily_dollar_volume is None):
            raise PolicyViolation("Missing capital evidence (portfolio/order value/ADV); buys blocked.")
        self.risk_limits.validate_purchase(
            proposal.portfolio, symbol=proposal.symbol, sector=proposal.sector,
            order_value=proposal.order_value,
            avg_daily_dollar_volume=proposal.avg_daily_dollar_volume)

    def _check_shape(self, proposal: TradeProposal) -> None:
        if self.require_limit_orders and proposal.order_type != "limit":
            raise PolicyViolation("Autonomous execution requires a limit order.")
        if proposal.time_in_force != "gfd" or proposal.extended_hours:
            raise PolicyViolation("Autonomous execution is regular-session DAY only.")
        if proposal.limit_price is None or proposal.limit_price <= 0:
            raise PolicyViolation("A positive limit price is required.")
        if proposal.side == "buy" and self.forbid_price_chasing:
            if proposal.intended_price is None:
                raise PolicyViolation("No reviewed intended price; cannot verify no-chase.")
            if proposal.limit_price != proposal.intended_price:
                raise PolicyViolation("Limit must equal the reviewed intended price (no chasing).")

    def _check_confidence(self, proposal: TradeProposal):
        if proposal.side != "buy":
            return "skip"
        if proposal.confidence is None:
            raise PolicyViolation("No confidence evidence; buys blocked.")
        if proposal.confidence < self.min_confidence:
            raise PolicyViolation(f"Confidence {proposal.confidence} below minimum {self.min_confidence}.")

    def _check_state(self, proposal: TradeProposal, today: date, database) -> None:
        if database is None:
            raise PolicyViolation("No ledger handle; kill-switch/cooldown unverifiable.")
        database.require_not_killed()
        database.require_no_symbol_cooldown(proposal.symbol, today=today.isoformat())
