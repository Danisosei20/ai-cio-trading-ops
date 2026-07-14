from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport, AlpacaPaperTransport
from .database import CioDatabase, DATABASE_SCHEMA_VERSION
from .errors import PolicyViolation
from .risk import RiskLimits
from .service import RobinhoodTradingService
from .settings import load_config, load_env, validate_config_shape


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

    def require_live_trading(self) -> None:
        if self.mode != "live_approval" or not self.trading_enabled:
            raise PolicyViolation("Live trading kill switch is off; use paper mode or explicitly enable live trading.")

    @property
    def paper_auto(self) -> bool:
        return self.mode == "paper_auto"

    def require_paper_trading(self) -> None:
        if self.mode != "paper_auto" or self.paper_broker != "alpaca":
            raise PolicyViolation("Paper placement requires paper_auto mode with the Alpaca paper broker.")


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
    return RuntimeSettings(
        mode=mode,
        trading_enabled=env.get("TRADING_ENABLED", "false").lower() == "true",
        channel_id=config["channels"]["slack"]["channel_id"],
        approval_window_minutes=int(config["approval_window_minutes"]),
        database_path=database_path,
        dashboard_path=dashboard_path,
        risk_limits=RiskLimits(
            max_position_weight=Decimal(str(risk["max_position_weight"])),
            max_sector_weight=Decimal(str(risk["max_sector_weight"])),
            min_cash_weight=Decimal(str(risk["minimum_cash_weight"])),
            max_daily_approved_capital=Decimal(str(risk["max_daily_approved_capital_usd"])),
            max_pending_approvals=int(risk["max_pending_approvals"]),
            max_spread_pct=Decimal(str(risk["max_bid_ask_spread_pct"])),
            max_order_pct_avg_volume=Decimal(str(risk["max_order_pct_average_daily_volume"])),
            max_order_value=Decimal(str(risk.get("max_order_value_usd", "999999999"))),
            min_cash_dollars=Decimal(str(risk.get("minimum_cash_reserve_usd", "0"))),
            max_open_positions=int(risk.get("max_open_positions", 999999)),
            max_daily_loss=Decimal(str(risk.get("max_daily_loss_usd", "999999999"))),
            max_weekly_loss=Decimal(str(risk.get("max_weekly_loss_usd", "999999999"))),
        ),
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


def build_database(settings: RuntimeSettings) -> CioDatabase:
    return CioDatabase(settings.database_path)


def build_live_service(backend, *, settings: RuntimeSettings, sp500_snapshot, authorizer=None):
    """The only production service factory; always attaches the live kill switch."""
    if settings.mode != "live_approval" or settings.live_broker != "robinhood":
        raise PolicyViolation("Robinhood service is permitted only in live_approval mode.")
    return RobinhoodTradingService(
        backend, authorizer=authorizer, approval_store=build_database(settings),
        sp500_snapshot=sp500_snapshot, execution_guard=settings.require_live_trading,
    )


def build_paper_service(
    *, settings: RuntimeSettings, sp500_snapshot, env_path=".env", authorizer=None,
    transport: AlpacaPaperTransport | None = None,
):
    """Build an Alpaca-only paper service; the transport rejects every live Alpaca URL."""
    settings.require_paper_trading()
    values = {**load_env(env_path), **os.environ}
    paper_transport = transport or AlpacaPaperHttpTransport.from_values(values)
    return RobinhoodTradingService(
        AlpacaPaperBackend(paper_transport), authorizer=authorizer,
        approval_store=build_database(settings), sp500_snapshot=sp500_snapshot,
        execution_guard=settings.require_paper_trading,
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
