from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .database import CioDatabase
from .errors import PolicyViolation
from .risk import RiskLimits
from .service import RobinhoodTradingService
from .settings import load_config, load_env


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

    def require_live_trading(self) -> None:
        if self.mode != "live_approval" or not self.trading_enabled:
            raise PolicyViolation("Live trading kill switch is off; use paper mode or explicitly enable live trading.")

    @property
    def paper_auto(self) -> bool:
        return self.mode == "paper_auto"


def build_settings(config_path="config/approval_routes.json", env_path=".env") -> RuntimeSettings:
    config = load_config(config_path, env_path)
    env = load_env(env_path)
    risk = config["risk_limits"]
    mode = env.get("TRADING_MODE", "research_only").lower()
    aliases = {"paper": "paper_auto", "live": "live_approval"}
    mode = aliases.get(mode, mode)
    if mode not in {"research_only", "paper_auto", "live_approval"}:
        raise PolicyViolation("TRADING_MODE must be research_only, paper_auto, or live_approval.")
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
    )


def build_database(settings: RuntimeSettings) -> CioDatabase:
    return CioDatabase(settings.database_path)


def build_live_service(backend, *, settings: RuntimeSettings, sp500_snapshot, authorizer=None):
    """The only production service factory; always attaches the live kill switch."""
    return RobinhoodTradingService(
        backend, authorizer=authorizer, approval_store=build_database(settings),
        sp500_snapshot=sp500_snapshot, execution_guard=settings.require_live_trading,
    )
