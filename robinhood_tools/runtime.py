from __future__ import annotations

from dataclasses import dataclass
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
    return RuntimeSettings(
        mode=mode,
        trading_enabled=env.get("TRADING_ENABLED", "false").lower() == "true",
        channel_id=config["channels"]["slack"]["channel_id"],
        approval_window_minutes=int(config["approval_window_minutes"]),
        database_path=Path(config["runtime"]["database_path"]),
        dashboard_path=Path(config["runtime"]["dashboard_path"]),
        risk_limits=RiskLimits(
            max_position_weight=Decimal(str(risk["max_position_weight"])),
            max_sector_weight=Decimal(str(risk["max_sector_weight"])),
            min_cash_weight=Decimal(str(risk["minimum_cash_weight"])),
            max_daily_approved_capital=Decimal(str(risk["max_daily_approved_capital_usd"])),
            max_pending_approvals=int(risk["max_pending_approvals"]),
            max_spread_pct=Decimal(str(risk["max_bid_ask_spread_pct"])),
            max_order_pct_avg_volume=Decimal(str(risk["max_order_pct_average_daily_volume"])),
        ),
    )


def build_database(settings: RuntimeSettings) -> CioDatabase:
    return CioDatabase(settings.database_path)


def build_live_service(backend, *, settings: RuntimeSettings, sp500_snapshot, authorizer=None):
    """The only production service factory; always attaches the live kill switch."""
    return RobinhoodTradingService(
        backend, authorizer=authorizer, approval_store=build_database(settings),
        sp500_snapshot=sp500_snapshot, execution_guard=settings.require_live_trading,
    )
