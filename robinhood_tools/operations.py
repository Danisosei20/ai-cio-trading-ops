from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from .database import CioDatabase
from .errors import PolicyViolation
from .models import Order
from .reconciliation import Fill, ReconciliationResult, reconcile_order
from .slack_replies import parse_safe_reply, reply_acknowledgement, transition_for_reply


class OrderStatusHost(Protocol):
    def get_order(self, *, account_id: str, order_id: str) -> Order: ...
    def get_fills(self, *, account_id: str, order_id: str) -> list[Fill]: ...


class SlackReplyHost(Protocol):
    def replies(self, *, channel_id: str, parent_message_ts: str) -> list[dict]: ...
    def acknowledge(self, *, channel_id: str, parent_message_ts: str, message: str) -> None: ...


class HealthNotifier(Protocol):
    def send_health_alert(self, *, message: str) -> None: ...


@dataclass(frozen=True)
class PollResult:
    terminal: bool
    reconciliation: ReconciliationResult


@dataclass(frozen=True)
class SlackMonitorResult:
    state: str
    outcomes: tuple[dict, ...]


@dataclass(frozen=True)
class RecoveryPlan:
    stale_daily_run_keys: tuple[str, ...]
    open_slack_approval_ids: tuple[str, ...]
    reconciliation_approval_ids: tuple[str, ...]

    @property
    def work_required(self) -> bool:
        return bool(
            self.stale_daily_run_keys or self.open_slack_approval_ids or self.reconciliation_approval_ids
        )


def build_recovery_plan(database: CioDatabase) -> RecoveryPlan:
    """Return durable restart work in the required fail-closed order."""
    return RecoveryPlan(
        stale_daily_run_keys=tuple(row["run_key"] for row in database.stale_daily_runs()),
        open_slack_approval_ids=tuple(row["approval_id"] for row in database.open_reply_windows()),
        reconciliation_approval_ids=tuple(
            row["approval_id"] for row in database.list_reconciliation_required()
        ),
    )


def poll_order_once(database: CioDatabase, host: OrderStatusHost, *, account_id: str,
                    order_id: str) -> PollResult:
    order = host.get_order(account_id=account_id, order_id=order_id)
    result = reconcile_order(database, order, host.get_fills(account_id=account_id, order_id=order_id))
    return PollResult(order.status in {"filled", "cancelled", "rejected"}, result)


def poll_until_terminal(database: CioDatabase, host: OrderStatusHost, *, account_id: str,
                        order_id: str, attempts: int = 20, interval_seconds: float = 3) -> PollResult:
    if attempts < 1 or interval_seconds < 0:
        raise PolicyViolation("Polling attempts must be positive and interval cannot be negative.")
    result = poll_order_once(database, host, account_id=account_id, order_id=order_id)
    for _ in range(attempts - 1):
        if result.terminal:
            return result
        time.sleep(interval_seconds)
        result = poll_order_once(database, host, account_id=account_id, order_id=order_id)
    return result


def monitor_slack_replies_once(database: CioDatabase, host: SlackReplyHost, *, approval_id: str,
                               channel_id: str, parent_message_ts: str) -> list[dict]:
    outcomes: list[dict] = []
    for message in host.replies(channel_id=channel_id, parent_message_ts=parent_message_ts):
        message_ts = str(message.get("message_ts") or message.get("ts") or "")
        if not message_ts:
            continue
        parsed = parse_safe_reply(str(message.get("text", "")))
        if not database.claim_slack_message(channel_id, message_ts, parsed.kind):
            continue
        transition = transition_for_reply(parsed)
        if transition.should_reject:
            database.reject(approval_id)
            database.resolve_reply_window(approval_id, "rejected")
        host.acknowledge(
            channel_id=channel_id, parent_message_ts=parent_message_ts,
            message=reply_acknowledgement(parsed),
        )
        database.mark_slack_message_acknowledged(channel_id, message_ts)
        outcomes.append({
            "message_ts": message_ts, "state": transition.state,
            "value": str(parsed.value) if parsed.value is not None else None,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        })
    return outcomes


def monitor_slack_reply_window(
    database: CioDatabase,
    host: SlackReplyHost,
    *,
    approval_id: str,
    channel_id: str,
    parent_message_ts: str,
    timeout_seconds: float = 600,
    poll_interval_seconds: float = 5,
) -> SlackMonitorResult:
    """Event-scoped monitor for one approval thread; never grants execution authority."""
    if timeout_seconds < 0 or poll_interval_seconds <= 0:
        raise PolicyViolation("Slack monitor timeout cannot be negative and poll interval must be positive.")
    started = time.monotonic()
    all_outcomes: list[dict] = []
    while True:
        outcomes = monitor_slack_replies_once(
            database, host, approval_id=approval_id, channel_id=channel_id,
            parent_message_ts=parent_message_ts,
        )
        all_outcomes.extend(outcomes)
        if any(item["state"] == "rejected" for item in outcomes):
            database.cleanup_terminal_reply_windows()
            return SlackMonitorResult("rejected", tuple(all_outcomes))
        if any(item["state"] == "fresh_review_required" for item in outcomes):
            database.resolve_reply_window(approval_id, "responded")
            return SlackMonitorResult("fresh_review_required", tuple(all_outcomes))
        if time.monotonic() - started >= timeout_seconds:
            rejected = database.reject_expired_reply_windows()
            database.cleanup_terminal_reply_windows()
            state = "expired" if approval_id in rejected else "timeout"
            return SlackMonitorResult(state, tuple(all_outcomes))
        time.sleep(poll_interval_seconds)


def resume_open_slack_monitors(
    database: CioDatabase,
    host: SlackReplyHost,
    *,
    timeout_seconds: float = 600,
    poll_interval_seconds: float = 5,
) -> dict[str, SlackMonitorResult]:
    """Resume every unexpired persisted monitor after process restart."""
    results: dict[str, SlackMonitorResult] = {}
    for window in database.open_reply_windows():
        parent_ts = window.get("parent_message_ts")
        if not parent_ts:
            continue
        results[window["approval_id"]] = monitor_slack_reply_window(
            database, host, approval_id=window["approval_id"], channel_id=window["channel_id"],
            parent_message_ts=parent_ts, timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    return results


def report_service_failure(notifier: HealthNotifier, *, component: str, error: Exception) -> None:
    """Report scheduler/connector failures through an independently supplied notifier."""
    notifier.send_health_alert(
        message=f"AI CIO health alert: {component} failed ({type(error).__name__}). Manual review required."
    )


def fills_total(fills: list[dict]) -> tuple[Decimal, Decimal, Decimal]:
    quantity = sum((Decimal(row["quantity"]) for row in fills), Decimal("0"))
    proceeds = sum((Decimal(row["quantity"]) * Decimal(row["price"]) for row in fills), Decimal("0"))
    fees = sum((Decimal(row["fee"]) for row in fills), Decimal("0"))
    return quantity, proceeds, fees


def apply_losing_exit_cooldown(database: CioDatabase, *, symbol: str, realized_profit: Decimal,
                               thesis_invalidated: bool, starts_on: str, expires_on: str) -> bool:
    if realized_profit < 0 and thesis_invalidated:
        database.add_symbol_cooldown(symbol, reason="losing exit with invalidated thesis",
                                     starts_on=starts_on, expires_on=expires_on)
        return True
    return False
