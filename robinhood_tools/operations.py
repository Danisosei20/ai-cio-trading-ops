from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from .database import CioDatabase
from .errors import PolicyViolation
from .models import Order
from .reconciliation import Fill, ReconciliationResult, reconcile_order


class OrderStatusHost(Protocol):
    def get_order(self, *, account_id: str, order_id: str) -> Order: ...
    def get_fills(self, *, account_id: str, order_id: str) -> list[Fill]: ...


class HealthNotifier(Protocol):
    def send_health_alert(self, *, message: str) -> None: ...


@dataclass(frozen=True)
class PollResult:
    terminal: bool
    reconciliation: ReconciliationResult


@dataclass(frozen=True)
class RecoveryPlan:
    stale_daily_run_keys: tuple[str, ...]
    reconciliation_approval_ids: tuple[str, ...]

    @property
    def work_required(self) -> bool:
        return bool(self.stale_daily_run_keys or self.reconciliation_approval_ids)


def build_recovery_plan(database: CioDatabase) -> RecoveryPlan:
    """Return durable restart work in the required fail-closed order."""
    return RecoveryPlan(
        stale_daily_run_keys=tuple(row["run_key"] for row in database.stale_daily_runs()),
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
