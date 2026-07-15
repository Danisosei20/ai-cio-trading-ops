from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .errors import PolicyViolation

VARIABLE = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")

CONFIG_SCHEMA: dict[str, Any] = {
    "mode": None,
    "timezone": None,
    "approval_window_minutes": None,
    "personal_context": {
        "account_nickname": None, "investment_objective": None,
        "risk_tolerance": None, "tax_context": None,
    },
    "investment_policy": {
        "strategy_version": None, "purchase_universe": None, "membership_max_age_hours": None,
        "require_live_quote": None, "require_current_news": None,
        "minimum_independent_research_sources": None, "require_volume_liquidity_analysis": None,
        "require_execution_quality_analysis": None, "require_trend_volatility_event_analysis": None,
        "learning_checkpoints_trading_days": None, "minimum_observations_before_policy_change": None,
        "automatic_profit_sales": None, "profitable_positions_action": None,
    },
    "risk_limits": {
        "max_order_value_usd": None, "max_symbol_exposure_usd": None,
        "minimum_cash_reserve_usd": None, "max_open_positions": None,
        "max_daily_loss_usd": None, "max_weekly_loss_usd": None, "max_position_weight": None,
        "max_sector_weight": None, "minimum_cash_weight": None, "max_daily_approved_capital_usd": None,
        "max_pending_approvals": None, "max_bid_ask_spread_pct": None,
        "max_order_pct_average_daily_volume": None,
    },
    "entry_controls": {
        "earnings_blackout_trading_days": None, "loss_cooldown_trading_days": None,
        "require_data_quality_score": None, "risk_on_minimum_score": None,
        "neutral_minimum_score": None, "risk_off_minimum_score": None,
    },
    "paper_autonomy": {
        "enabled": None, "human_approval_required": None, "max_order_value_usd": None,
        "max_symbol_exposure_usd": None, "regular_session_only": None,
        "earliest_entry_time_et": None, "latest_entry_time_et": None,
        "require_limit_orders": None, "forbid_price_chasing": None,
        "notify_slack_after_execution": None, "tradingview_confirmation_when_available": None,
        "panic_entry_minimum_relative_volume": None, "panic_entry_minimum_stabilization_bars": None,
        "panic_entry_minimum_reward_risk": None,
    },
    "runtime": {
        "database_path": None, "dashboard_path": None, "paper_database_path": None,
        "paper_dashboard_path": None, "live_database_path": None, "live_dashboard_path": None,
        "paper_broker": None, "live_broker": None,
        "structured_logging": None, "broker_reconciliation_required_after_uncertain_failure": None,
        "database_schema_version": None, "lifecycle_lease_seconds": None,
        "backup_before_migration": None, "audit_retention_days": None, "delivery_retention_days": None,
    },
    "schedule": {
        "enabled": None, "time_local": None, "timezone": None, "weekdays_only": None,
        "require_official_market_calendar_open_day": None, "skip_message_when_market_closed": None,
    },
    "watchdog": {
        "enabled": None, "grace_minutes": None, "independent_host_required": None,
        "deduplicate_health_alerts": None,
    },
    "data_freshness_max_age_minutes": {
        "broker_account": None, "positions_orders_fills": None, "quotes_spreads_volume": None,
        "market_regime": None, "earnings_corporate_events": None, "sp500_membership": None,
        "research_news_filings": None,
    },
    "shadow_equity": {
        "enabled": None, "paper_only": None, "maximum_daily_candidates": None,
        "allow_no_action": None, "learning_checkpoints_trading_days": None,
    },
    "broker_state_drift": {
        "require_reconciliation_before_recommendation": None, "check_positions": None,
        "check_open_orders": None, "check_fills": None, "check_dividends_and_corporate_actions": None,
    },
    "cash_accounting": {
        "use_settled_cash": None, "subtract_unsettled_cash": None,
        "reserve_pending_order_commitments": None,
    },
    "daily_message": {
        "action_first": None, "show_what_to_do": None, "show_changed_since_yesterday": None,
        "show_data_as_of": None, "label_watchlist_monitoring_only": None,
    },
    "slack_reply_monitor": {
        "enabled": None, "mode": None, "window_minutes_after_message": None,
        "reject_when_window_expires_without_response": None, "cleanup_after_terminal_state": None,
        "read_channel_and_threads": None, "deduplicate_by_channel_and_message_ts": None,
        "safe_test_commands": None, "no_may_reject_linked_pending_approval": None,
        "yes_requests_sizing_but_never_approves": None, "execution_commands_from_slack": None,
    },
    "channels": {
        "slack": {"enabled": None, "channel_id": None, "required_tools": None},
        "health_slack": {"enabled": None, "channel_id": None},
        "phone": {"enabled": None, "method": None, "to": None},
    },
    "rules": {
        "send_no_action_summary": None, "send_trade_approval_requests": None,
        "never_place_without_confirmed_approval": None, "allowed_account_policy": None,
        "default_trade_action": None,
    },
}


def load_env(path: str | Path = ".env") -> dict[str, str]:
    """Load simple KEY=VALUE settings without overwriting process environment."""
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for line_number, raw in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PolicyViolation(f"Invalid .env line {line_number}: expected KEY=VALUE.")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise PolicyViolation(f"Invalid .env variable name {key!r} on line {line_number}.")
        values[key] = value.strip().strip('"').strip("'")
    return values


def load_config(path: str | Path, env_path: str | Path = ".env") -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    variables = {**load_env(env_path), **os.environ}
    return _resolve(data, variables)


def validate_config_shape(config: dict[str, Any]) -> None:
    """Reject missing, misspelled, or stale configuration fields before startup."""
    _validate_mapping(config, CONFIG_SCHEMA, "config")


def _validate_mapping(value: Any, schema: dict[str, Any], path: str) -> None:
    if not isinstance(value, dict):
        raise PolicyViolation(f"{path} must be an object.")
    missing = sorted(set(schema) - set(value))
    unknown = sorted(set(value) - set(schema))
    if missing:
        raise PolicyViolation(f"{path} is missing required fields: {', '.join(missing)}.")
    if unknown:
        raise PolicyViolation(f"{path} contains unknown fields: {', '.join(unknown)}.")
    for key, child_schema in schema.items():
        if isinstance(child_schema, dict):
            _validate_mapping(value[key], child_schema, f"{path}.{key}")


def _resolve(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, variables) for item in value]
    if not isinstance(value, str):
        return value
    match = VARIABLE.fullmatch(value)
    if not match:
        return value
    name = match.group(1)
    if name not in variables or variables[name] == "":
        raise PolicyViolation(f"Required personal variable {name} is missing; set it in .env.")
    raw = variables[name]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
