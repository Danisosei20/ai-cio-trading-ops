from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport, AlpacaPaperTransport
from .database import CioDatabase, DATABASE_SCHEMA_VERSION
from .errors import PolicyViolation
from .risk import RiskLimits
from .service import RobinhoodTradingService
from .settings import load_config, load_env, validate_config_shape


@dataclass(frozen=True)
class PaperAutonomySettings:
    enabled: bool = False
    human_approval_required: bool = True
    regular_session_only: bool = True
    earliest_entry_time_et: str = "11:35"
    latest_entry_time_et: str = "15:30"
    require_limit_orders: bool = True
    forbid_price_chasing: bool = True
    notify_slack_after_execution: bool = True
    tradingview_confirmation_when_available: bool = True
    panic_entry_minimum_relative_volume: Decimal = Decimal("1.5")
    panic_entry_minimum_stabilization_bars: int = 3
    panic_entry_minimum_reward_risk: Decimal = Decimal("2.5")


@dataclass(frozen=True)
class RuntimeSettings:
    mode: str
    trading_enabled: bool
    channel_id: str
    approval_window_minutes: int
    database_path: Path
    dashboard_path: Path
    risk_limits: RiskLimits
    health_channel_id: str = ""
    timezone: str = "America/New_York"
    schedule_time_local: str = "09:45"
    watchdog_grace_minutes: int = 15
    freshness_max_age_minutes: dict[str, int] = field(default_factory=dict)
    paper_broker: str = "alpaca"
    live_broker: str = "robinhood"
    paper_trading_enabled: bool = False
    paper_autonomy: PaperAutonomySettings = field(default_factory=PaperAutonomySettings)
    entry_minimum_scores: dict[str, int] = field(
        default_factory=lambda: {"risk_on": 90, "neutral": 93, "risk_off": 97}
    )
    earnings_blackout_days: int = 5

    def require_live_trading(self) -> None:
        if self.mode != "live_approval" or not self.trading_enabled:
            raise PolicyViolation("Live trading kill switch is off; use paper mode or explicitly enable live trading.")

    @property
    def paper_auto(self) -> bool:
        return self.mode == "paper_auto"

    def require_paper_trading(self) -> None:
        if self.mode != "paper_auto" or self.paper_broker != "alpaca":
            raise PolicyViolation("Paper placement requires paper_auto mode with the Alpaca paper broker.")

    def require_paper_execution(self) -> None:
        self.require_paper_trading()
        if not self.paper_trading_enabled or not self.paper_autonomy.enabled:
            raise PolicyViolation("Autonomous Alpaca paper execution is disabled.")
        if self.paper_autonomy.regular_session_only:
            now_et = datetime.now(ZoneInfo("America/New_York"))
            earliest = datetime.strptime(self.paper_autonomy.earliest_entry_time_et, "%H:%M").time()
            latest = datetime.strptime(self.paper_autonomy.latest_entry_time_et, "%H:%M").time()
            if now_et.weekday() >= 5 or not earliest <= now_et.time().replace(tzinfo=None) <= latest:
                raise PolicyViolation(
                    "Autonomous Alpaca paper execution is outside the configured regular-session window."
                )

    def minimum_score_for_regime(self, regime: str) -> int:
        try:
            return self.entry_minimum_scores[regime]
        except KeyError as exc:
            raise PolicyViolation(f"Unknown market regime {regime!r}.") from exc


def build_settings(config_path="config/approval_routes.json", env_path=".env") -> RuntimeSettings:
    config = load_config(config_path, env_path)
    validate_config_shape(config)
    configured_schema = int(config["runtime"]["database_schema_version"])
    if configured_schema != DATABASE_SCHEMA_VERSION:
        raise PolicyViolation(
            f"Configured database schema {configured_schema} does not match code schema {DATABASE_SCHEMA_VERSION}."
        )
    env = load_env(env_path)
    risk = config["risk_limits"]
    mode = env.get("TRADING_MODE", "research_only").lower()
    aliases = {"paper": "paper_auto", "live": "live_approval"}
    mode = aliases.get(mode, mode)
    if mode not in {"research_only", "paper_auto", "live_approval"}:
        raise PolicyViolation("TRADING_MODE must be research_only, paper_auto, or live_approval.")
    paper_broker = str(config["runtime"]["paper_broker"]).lower()
    live_broker = str(config["runtime"]["live_broker"]).lower()
    if paper_broker != "alpaca" or live_broker != "robinhood":
        raise PolicyViolation("Broker routing is fixed: paper_broker=alpaca and live_broker=robinhood.")
    database_path = Path(config["runtime"]["database_path"])
    dashboard_path = Path(config["runtime"]["dashboard_path"])
    if mode == "paper_auto":
        database_path = Path(config["runtime"].get("paper_database_path", "outputs/paper/cio.db"))
        dashboard_path = Path(config["runtime"].get("paper_dashboard_path", "outputs/paper/dashboard.html"))
    elif mode == "live_approval":
        database_path = Path(config["runtime"].get("live_database_path", "outputs/live/cio.db"))
        dashboard_path = Path(config["runtime"].get("live_dashboard_path", "outputs/live/dashboard.html"))
    paper = config["paper_autonomy"]
    paper_autonomy = PaperAutonomySettings(
        enabled=bool(paper["enabled"]),
        human_approval_required=bool(paper["human_approval_required"]),
        regular_session_only=bool(paper["regular_session_only"]),
        earliest_entry_time_et=str(paper["earliest_entry_time_et"]),
        latest_entry_time_et=str(paper["latest_entry_time_et"]),
        require_limit_orders=bool(paper["require_limit_orders"]),
        forbid_price_chasing=bool(paper["forbid_price_chasing"]),
        notify_slack_after_execution=bool(paper["notify_slack_after_execution"]),
        tradingview_confirmation_when_available=bool(paper["tradingview_confirmation_when_available"]),
        panic_entry_minimum_relative_volume=Decimal(str(paper["panic_entry_minimum_relative_volume"])),
        panic_entry_minimum_stabilization_bars=int(paper["panic_entry_minimum_stabilization_bars"]),
        panic_entry_minimum_reward_risk=Decimal(str(paper["panic_entry_minimum_reward_risk"])),
    )
    risk_limits = _base_risk_limits(risk)
    if mode == "paper_auto":
        risk_limits = replace(
            risk_limits,
            max_order_value=Decimal(str(paper["max_order_value_usd"])),
            max_symbol_exposure=Decimal(str(paper["max_symbol_exposure_usd"])),
        )
    return RuntimeSettings(
        mode=mode,
        trading_enabled=env.get("TRADING_ENABLED", "false").lower() == "true",
        channel_id=config["channels"]["slack"]["channel_id"],
        approval_window_minutes=int(config["approval_window_minutes"]),
        database_path=database_path,
        dashboard_path=dashboard_path,
        risk_limits=risk_limits,
        paper_trading_enabled=env.get("PAPER_TRADING_ENABLED", "false").lower() == "true",
        paper_autonomy=paper_autonomy,
        entry_minimum_scores={
            "risk_on": int(config["entry_controls"]["risk_on_minimum_score"]),
            "neutral": int(config["entry_controls"]["neutral_minimum_score"]),
            "risk_off": int(config["entry_controls"]["risk_off_minimum_score"]),
        },
        earnings_blackout_days=int(config["entry_controls"]["earnings_blackout_trading_days"]),
        # The base/live risk profile remains independent from paper-only overrides.
        # Remaining fields follow below.
        health_channel_id=config.get("channels", {}).get("health_slack", {}).get("channel_id", ""),
        timezone=str(config.get("schedule", {}).get("timezone", "America/New_York")),
        schedule_time_local=str(config.get("schedule", {}).get("time_local", "09:45")),
        watchdog_grace_minutes=int(config.get("watchdog", {}).get("grace_minutes", 15)),
        freshness_max_age_minutes={
            str(key): int(value)
            for key, value in config.get("data_freshness_max_age_minutes", {}).items()
        },
        paper_broker=paper_broker,
        live_broker=live_broker,
    )


def _base_risk_limits(risk: dict) -> RiskLimits:
    return RiskLimits(
        max_position_weight=Decimal(str(risk["max_position_weight"])),
        max_sector_weight=Decimal(str(risk["max_sector_weight"])),
        min_cash_weight=Decimal(str(risk["minimum_cash_weight"])),
        max_daily_approved_capital=Decimal(str(risk["max_daily_approved_capital_usd"])),
        max_pending_approvals=int(risk["max_pending_approvals"]),
        max_spread_pct=Decimal(str(risk["max_bid_ask_spread_pct"])),
        max_order_pct_avg_volume=Decimal(str(risk["max_order_pct_average_daily_volume"])),
        max_order_value=Decimal(str(risk.get("max_order_value_usd", "999999999"))),
        max_symbol_exposure=Decimal(str(risk.get("max_symbol_exposure_usd", "999999999"))),
        min_cash_dollars=Decimal(str(risk.get("minimum_cash_reserve_usd", "0"))),
        max_open_positions=int(risk.get("max_open_positions", 999999)),
        max_daily_loss=Decimal(str(risk.get("max_daily_loss_usd", "999999999"))),
        max_weekly_loss=Decimal(str(risk.get("max_weekly_loss_usd", "999999999"))),
    )


def build_database(settings: RuntimeSettings) -> CioDatabase:
    return CioDatabase(settings.database_path)


def build_live_service(backend, *, settings: RuntimeSettings, sp500_snapshot, authorizer=None):
    """The only production service factory; always attaches the live kill switch."""
    if settings.mode != "live_approval" or settings.live_broker != "robinhood":
        raise PolicyViolation("Robinhood service is permitted only in live_approval mode.")
    return RobinhoodTradingService(
        backend, authorizer=authorizer, approval_store=build_database(settings),
        sp500_snapshot=sp500_snapshot, execution_guard=settings.require_live_trading,
        broker_environment="live", require_human_confirmation=True,
    )


def build_paper_service(
    *, settings: RuntimeSettings, sp500_snapshot, env_path=".env", authorizer=None,
    transport: AlpacaPaperTransport | None = None,
):
    """Build an Alpaca-only paper service; the transport rejects every live Alpaca URL."""
    settings.require_paper_trading()
    values = {**load_env(env_path), **os.environ}
    paper_transport = transport or AlpacaPaperHttpTransport.from_values(values)
    paper_backend = AlpacaPaperBackend(paper_transport)

    def require_open_paper_session() -> None:
        settings.require_paper_execution()
        if settings.paper_autonomy.regular_session_only and not paper_backend.market_clock().get("is_open"):
            raise PolicyViolation("Autonomous Alpaca paper execution requires the official market clock open.")

    return RobinhoodTradingService(
        paper_backend, authorizer=authorizer,
        approval_store=build_database(settings), sp500_snapshot=sp500_snapshot,
        execution_guard=require_open_paper_session, broker_environment="paper",
        require_human_confirmation=settings.paper_autonomy.human_approval_required,
    )


def build_mode_service(
    *, settings: RuntimeSettings, sp500_snapshot, env_path=".env", robinhood_backend=None,
    authorizer=None, alpaca_transport: AlpacaPaperTransport | None = None,
):
    """Route paper mode to Alpaca and live mode to Robinhood without fallback."""
    if settings.mode == "paper_auto":
        return build_paper_service(
            settings=settings, sp500_snapshot=sp500_snapshot, env_path=env_path,
            authorizer=authorizer, transport=alpaca_transport,
        )
    if settings.mode == "live_approval":
        if robinhood_backend is None:
            raise PolicyViolation("Robinhood backend is required in live_approval mode.")
        return build_live_service(
            robinhood_backend, settings=settings, sp500_snapshot=sp500_snapshot, authorizer=authorizer,
        )
    raise PolicyViolation("research_only mode does not create a broker trading service.")
