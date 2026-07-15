from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from .analysis import ExitPlan, Signal, TradeCandidate
from .database import CioDatabase
from .errors import PolicyViolation
from .lifecycle import ExitDecision
from .models import EquityOrderRequest, Order, OrderReview
from .risk import PortfolioState
from .runtime import PaperAutonomySettings, RuntimeSettings
from .safety import require_earnings_clear
from .service import RobinhoodTradingService


class PaperNotifier(Protocol):
    def send_approval(self, *, channel_id: str, message: str) -> dict: ...


class PaperJournal(Protocol):
    def append(self, event: dict) -> None: ...


@dataclass(frozen=True)
class PaperEntryContext:
    observed_at: str
    market_is_open: bool
    chart_confirmation: Signal
    chart_source: str
    material_negative_news: bool
    tradingview_confirmation: Signal | None = None
    panic_selloff: bool = False
    reclaimed_vwap_or_opening_range: bool = False
    stabilization_bars: int = 0

    def validate(
        self, policy: PaperAutonomySettings, candidate: TradeCandidate,
    ) -> None:
        self.validate_session(policy)
        if self.chart_confirmation != "supportive" or not self.chart_source.strip():
            raise PolicyViolation("A supportive, source-identified chart confirmation is required.")
        if self.tradingview_confirmation == "conflicting":
            raise PolicyViolation("TradingView conflicts with the primary chart analysis; do not enter.")
        if self.material_negative_news:
            raise PolicyViolation("Material negative company news blocks an autonomous paper entry.")
        if self.panic_selloff:
            if candidate.snapshot.relative_volume < policy.panic_entry_minimum_relative_volume:
                raise PolicyViolation("Panic-sell entries require elevated relative volume.")
            if not self.reclaimed_vwap_or_opening_range:
                raise PolicyViolation("Panic-sell entries require a VWAP or opening-range reclaim.")
            if self.stabilization_bars < policy.panic_entry_minimum_stabilization_bars:
                raise PolicyViolation("Panic-sell entries require multiple completed stabilization bars.")
            if candidate.reward_risk < policy.panic_entry_minimum_reward_risk:
                raise PolicyViolation("Panic-sell entries require the higher configured reward/risk hurdle.")

    def validate_session(self, policy: PaperAutonomySettings) -> None:
        observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise PolicyViolation("Paper entry observation time must include a timezone.")
        observed_et = observed.astimezone(ZoneInfo("America/New_York"))
        earliest = _parse_time(policy.earliest_entry_time_et)
        latest = _parse_time(policy.latest_entry_time_et)
        current = observed_et.time().replace(tzinfo=None)
        if policy.regular_session_only and not self.market_is_open:
            raise PolicyViolation("Autonomous paper entries require the official regular market session to be open.")
        if current < earliest or current > latest:
            raise PolicyViolation(
                f"Autonomous paper entries are allowed only from {policy.earliest_entry_time_et} "
                f"to {policy.latest_entry_time_et} ET."
            )


@dataclass(frozen=True)
class PaperExecutionResult:
    order: Order
    policy_authorization_id: str
    review_id: str
    order_fingerprint: str
    notification_status: str


class PaperAutoExecutor:
    """Autonomous paper-only execution that retains fingerprints and atomic deduplication."""

    def __init__(
        self,
        service: RobinhoodTradingService,
        database: CioDatabase,
        notifier: PaperNotifier,
        journal: PaperJournal,
        settings: RuntimeSettings,
    ):
        self.service = service
        self.database = database
        self.notifier = notifier
        self.journal = journal
        self.settings = settings

    def execute_purchase(
        self,
        *,
        request: EquityOrderRequest,
        candidate: TradeCandidate,
        portfolio: PortfolioState,
        sector: str,
        exit_plan: ExitPlan,
        context: PaperEntryContext,
    ) -> PaperExecutionResult:
        correlation_id = str(uuid.uuid4())
        self._require_paper_autonomy()
        self.database.require_not_killed()
        self.database.require_no_symbol_cooldown(request.symbol, today=date.today().isoformat())
        self._validate_entry_request(request, candidate)
        context.validate(self.settings.paper_autonomy, candidate)
        snapshot_observed = datetime.fromisoformat(candidate.snapshot.observed_at.replace("Z", "+00:00"))
        context_observed = datetime.fromisoformat(context.observed_at.replace("Z", "+00:00"))
        if abs((snapshot_observed - context_observed).total_seconds()) > 60:
            raise PolicyViolation("Paper session and market snapshot timestamps must match within one minute.")
        candidate.snapshot.validate(
            max_age_minutes=self.settings.freshness_max_age_minutes.get("quotes_spreads_volume", 5),
        )
        try:
            earnings_date = date.fromisoformat(candidate.snapshot.next_earnings_date or "")
        except ValueError as exc:
            raise PolicyViolation("A valid next earnings date is required for autonomous paper entry.") from exc
        require_earnings_clear(
            today=date.today(), earnings_date=earnings_date,
            blackout_days=self.settings.earnings_blackout_days,
        )
        candidate.validate(
            max_spread_pct=self.settings.risk_limits.max_spread_pct,
            max_position_weight=self.settings.risk_limits.max_position_weight,
            minimum_score=self.settings.minimum_score_for_regime(candidate.market_regime),
        )
        order_value = _order_value(request, candidate.intended_price)
        self.settings.risk_limits.validate_purchase(
            portfolio,
            symbol=request.symbol,
            sector=sector,
            order_value=order_value,
            avg_daily_dollar_volume=candidate.snapshot.avg_daily_dollar_volume,
        )

        review = self.service.review_equity_order(request)
        authorization = self.database.create(
            request, review, window_minutes=self.settings.approval_window_minutes,
        )
        self.database.approve(authorization.approval_id)
        self.database.audit(
            "paper_policy_authorized",
            {
                "symbol": request.symbol,
                "score": candidate.score,
                "market_regime": candidate.market_regime,
                "snapshot_id": candidate.snapshot.digest(),
                "human_approval_required": False,
            },
            correlation_id=correlation_id,
            approval_id=authorization.approval_id,
        )
        self._attach_lifecycle(request.symbol, authorization.approval_id)
        self.database.save_exit_plan(exit_plan.symbol, asdict(exit_plan))
        self.database.schedule_learning(
            authorization.approval_id,
            {day: _add_weekdays(date.today(), day).isoformat() for day in (1, 5, 20)},
        )
        self.journal.append({
            "event": "paper_policy_authorization",
            "approval_id": authorization.approval_id,
            "symbol": request.symbol,
            "snapshot_id": candidate.snapshot.digest(),
            "score": candidate.score,
        })

        order = self.service.place_equity_order(
            request,
            review_id=review.review_id,
            approval_id=authorization.approval_id,
            confirmed=False,
        )
        if order.status == "filled":
            self.database.mark_position_open(request.symbol, order_id=order.id)
        self.journal.append({
            "event": "paper_placement",
            "approval_id": authorization.approval_id,
            "order_id": order.id,
            "status": order.status,
            "symbol": request.symbol,
        })
        notification_status = self._notify_after_purchase(
            request=request,
            candidate=candidate,
            context=context,
            portfolio=portfolio,
            order_value=order_value,
            exit_plan=exit_plan,
            authorization_id=authorization.approval_id,
            review=review,
            order=order,
            correlation_id=correlation_id,
        )
        return PaperExecutionResult(
            order,
            authorization.approval_id,
            review.review_id,
            self.database.get(authorization.approval_id).order_fingerprint,
            notification_status,
        )

    def execute_sale(
        self,
        *,
        request: EquityOrderRequest,
        decision: ExitDecision,
        context: PaperEntryContext,
        estimated_profit: Decimal,
    ) -> PaperExecutionResult:
        correlation_id = str(uuid.uuid4())
        self._require_paper_autonomy()
        self.database.require_not_killed()
        if request.side != "sell" or decision.action == "hold":
            raise PolicyViolation("Autonomous paper sale requires a non-hold sell decision.")
        self._validate_limit_request(request)
        context.validate_session(self.settings.paper_autonomy)
        lifecycle = self.database.get_trade_lifecycle(request.symbol)
        if lifecycle["status"] != "open":
            raise PolicyViolation("A paper sale must continue the symbol's open lifecycle.")
        review = self.service.review_equity_order(request)
        authorization = self.database.create(
            request, review, window_minutes=self.settings.approval_window_minutes,
        )
        self.database.approve(authorization.approval_id)
        self.database.mark_sell_pending(request.symbol, approval_id=authorization.approval_id)
        self.database.audit(
            "paper_exit_policy_authorized",
            {"symbol": request.symbol, "reason": decision.reason, "human_approval_required": False},
            correlation_id=correlation_id,
            approval_id=authorization.approval_id,
        )
        order = self.service.place_equity_order(
            request,
            review_id=review.review_id,
            approval_id=authorization.approval_id,
            confirmed=False,
        )
        self.journal.append({
            "event": "paper_sale_placement",
            "approval_id": authorization.approval_id,
            "order_id": order.id,
            "status": order.status,
            "symbol": request.symbol,
            "estimated_profit_not_realized": str(estimated_profit),
        })
        notification_status = self._notify(
            authorization.approval_id,
            correlation_id,
            (
                f"**AI CIO — PAPER EXIT SUBMITTED**\n"
                f"- Symbol: **{request.symbol.upper()}**\n"
                f"- Status: **{order.status}**\n"
                f"- Limit price: **${request.limit_price}**\n"
                f"- Reason: {decision.reason}\n"
                f"- Estimated profit: **${estimated_profit}** — not realized until a filled sale is reconciled\n"
                f"- Broker order: `{order.id}`\n"
                "- Environment: Alpaca paper only; no live funds were used."
            ),
        )
        return PaperExecutionResult(
            order,
            authorization.approval_id,
            review.review_id,
            self.database.get(authorization.approval_id).order_fingerprint,
            notification_status,
        )

    def _require_paper_autonomy(self) -> None:
        self.settings.require_paper_execution()
        if self.settings.paper_autonomy.human_approval_required:
            raise PolicyViolation("Paper autonomy cannot run while human approval is required.")
        if self.service.broker_environment != "paper" or self.service.require_human_confirmation:
            raise PolicyViolation("Paper autonomy requires the isolated no-human-confirmation paper service.")

    def _validate_entry_request(self, request: EquityOrderRequest, candidate: TradeCandidate) -> None:
        if request.side != "buy":
            raise PolicyViolation("Autonomous paper entry accepts buy requests only.")
        self._validate_limit_request(request)
        if self.settings.paper_autonomy.forbid_price_chasing and request.limit_price != candidate.intended_price:
            raise PolicyViolation("Paper limit price must equal the reviewed intended price; price chasing is blocked.")

    def _validate_limit_request(self, request: EquityOrderRequest) -> None:
        if self.settings.paper_autonomy.require_limit_orders and request.order_type != "limit":
            raise PolicyViolation("Autonomous paper execution requires a limit order.")
        if request.time_in_force != "gfd" or request.extended_hours:
            raise PolicyViolation("Autonomous paper execution is regular-session DAY only.")
        if request.limit_price is None or request.limit_price <= 0:
            raise PolicyViolation("Autonomous paper execution requires a positive limit price.")

    def _attach_lifecycle(self, symbol: str, authorization_id: str) -> None:
        try:
            lifecycle = self.database.get_trade_lifecycle(symbol)
        except PolicyViolation:
            self.database.begin_trade_lifecycle(symbol, buy_approval_id=authorization_id)
            return
        if lifecycle["status"] != "research":
            raise PolicyViolation("The symbol already has an active non-research lifecycle.")
        self.database.attach_buy_authorization(symbol, approval_id=authorization_id)

    def _notify_after_purchase(
        self,
        *,
        request: EquityOrderRequest,
        candidate: TradeCandidate,
        context: PaperEntryContext,
        portfolio: PortfolioState,
        order_value: Decimal,
        exit_plan: ExitPlan,
        authorization_id: str,
        review: OrderReview,
        order: Order,
        correlation_id: str,
    ) -> str:
        sources = "\n".join(f"- [{item.title}]({item.url})" for item in candidate.snapshot.sources)
        tradingview = context.tradingview_confirmation or "not available"
        panic = "confirmed capitulation recovery" if context.panic_selloff else "not a panic-sell setup"
        message = (
            f"**AI CIO — PAPER TRADE SUBMITTED**\n"
            f"- Symbol: **{request.symbol.upper()}**\n"
            f"- Side: **BUY**\n"
            f"- Size: **${order_value}** maximum reviewed cost\n"
            f"- Limit price: **${request.limit_price}** — no fill above this price\n"
            f"- Broker status: **{order.status}**\n"
            f"- Score/regime: **{candidate.score}/100 · {candidate.market_regime}**\n"
            f"- Why: {candidate.thesis}\n"
            f"- Counterpoint: {candidate.counter_argument}\n"
            f"- Liquidity: **${candidate.snapshot.avg_daily_dollar_volume}** average daily dollar volume; "
            f"spread **{candidate.snapshot.spread_pct:.3%}**\n"
            f"- Chart: **{context.chart_confirmation}** from {context.chart_source}; "
            f"TradingView cross-check: **{tradingview}**\n"
            f"- Panic-seller check: **{panic}**\n"
            f"- Invalidation: **${candidate.invalidation_level}**\n"
            f"- Target/review: {candidate.target_or_review_condition}\n"
            f"- Exit plan: {exit_plan.invalidation_condition}; {exit_plan.earnings_event_plan}\n"
            f"- Cash after reviewed cost: **${portfolio.settled_cash_available - order_value}**\n"
            f"- Policy authorization: `{authorization_id}`\n"
            f"- Order fingerprint: `{self.database.get(authorization_id).order_fingerprint}`\n"
            f"- Broker review: `{review.review_id}`\n"
            f"- Broker order: `{order.id}`\n"
            "- Environment: **Alpaca paper only**; no live funds and no Slack approval were used.\n\n"
            f"**Sources**\n{sources}"
        )
        return self._notify(authorization_id, correlation_id, message)

    def _notify(self, authorization_id: str, correlation_id: str, message: str) -> str:
        if not self.settings.paper_autonomy.notify_slack_after_execution:
            return "disabled"
        attempted_at = datetime.now(timezone.utc).isoformat()
        try:
            delivery = self.notifier.send_approval(channel_id=self.settings.channel_id, message=message)
            self.database.record_delivery(
                approval_id=authorization_id,
                channel_id=self.settings.channel_id,
                status="sent",
                attempted_at=attempted_at,
                message_ts=delivery.get("message_ts"),
                message_link=delivery.get("message_link"),
                retry_count=int(delivery.get("retry_count", 0)),
            )
            self.database.audit(
                "paper_post_trade_slack_sent",
                {"message_ts": delivery.get("message_ts"), "message_link": delivery.get("message_link")},
                correlation_id=correlation_id,
                approval_id=authorization_id,
            )
            return "sent"
        except Exception as exc:
            self.database.record_delivery(
                approval_id=authorization_id,
                channel_id=self.settings.channel_id,
                status="failed",
                attempted_at=attempted_at,
                error=type(exc).__name__,
            )
            self.database.audit(
                "paper_post_trade_slack_failed",
                {"error": type(exc).__name__},
                correlation_id=correlation_id,
                approval_id=authorization_id,
            )
            return "failed"


def _order_value(request: EquityOrderRequest, intended_price: Decimal) -> Decimal:
    if request.notional is not None:
        return request.notional
    return (request.quantity or Decimal("0")) * (request.limit_price or intended_price)


def _parse_time(value: str):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise PolicyViolation("Paper entry times must use HH:MM 24-hour format.") from exc


def _add_weekdays(start: date, days: int) -> date:
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current
