from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

from .database import CioDatabase
from .errors import PolicyViolation


@dataclass(frozen=True)
class SourceFreshness:
    name: str
    observed_at: str | None
    max_age: timedelta
    required: bool = True

    def status(self, *, now: datetime) -> str:
        if not self.observed_at:
            return "missing" if self.required else "unavailable"
        observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise PolicyViolation(f"Freshness timestamp for {self.name} must include a timezone.")
        if observed > now + timedelta(minutes=1):
            return "future"
        return "fresh" if now - observed <= self.max_age else "stale"


@dataclass(frozen=True)
class FreshnessManifest:
    sources: tuple[SourceFreshness, ...]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def report(self, *, now: datetime | None = None) -> dict[str, dict[str, str | bool | None]]:
        current = now or datetime.now(timezone.utc)
        return {
            source.name: {
                "observed_at": source.observed_at,
                "max_age_seconds": str(int(source.max_age.total_seconds())),
                "required": source.required,
                "status": source.status(now=current),
            }
            for source in self.sources
        }

    def require_complete(self, *, now: datetime | None = None) -> dict[str, dict[str, str | bool | None]]:
        report = self.report(now=now)
        failures = [name for name, item in report.items() if item["required"] and item["status"] != "fresh"]
        if failures:
            details = ", ".join(f"{name}={report[name]['status']}" for name in failures)
            raise PolicyViolation(f"Required data is incomplete or stale: {details}.")
        return report


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    quantity: Decimal


@dataclass(frozen=True)
class BrokerOrderState:
    order_id: str
    symbol: str
    status: str


@dataclass(frozen=True)
class BrokerFillState:
    fill_id: str
    order_id: str
    symbol: str


@dataclass(frozen=True)
class BrokerEventState:
    event_id: str
    symbol: str
    event_type: str


@dataclass(frozen=True)
class BrokerStateSnapshot:
    observed_at: str
    positions: tuple[BrokerPosition, ...] = ()
    open_orders: tuple[BrokerOrderState, ...] = ()
    fills: tuple[BrokerFillState, ...] = ()
    events: tuple[BrokerEventState, ...] = ()


@dataclass(frozen=True)
class DriftIssue:
    kind: str
    identifier: str
    detail: str


def detect_broker_state_drift(database: CioDatabase, state: BrokerStateSnapshot) -> tuple[DriftIssue, ...]:
    actual_symbols = {item.symbol.upper() for item in state.positions if item.quantity != 0}
    expected_symbols = database.expected_open_symbols()
    known_order_ids = database.known_order_ids()
    known_fill_ids = database.known_fill_ids()
    known_event_ids = database.known_broker_event_ids()
    issues: list[DriftIssue] = []
    for symbol in sorted(actual_symbols - expected_symbols):
        issues.append(DriftIssue("unlinked_position", symbol, "Broker position has no open ticker lifecycle."))
    for symbol in sorted(expected_symbols - actual_symbols):
        issues.append(DriftIssue("missing_position", symbol, "Open ticker lifecycle has no broker position."))
    for order in state.open_orders:
        if order.order_id not in known_order_ids:
            issues.append(DriftIssue("unlinked_order", order.order_id, f"Unknown {order.status} order for {order.symbol}."))
    for fill in state.fills:
        if fill.fill_id not in known_fill_ids:
            issues.append(DriftIssue("unlinked_fill", fill.fill_id, f"Unknown fill for {fill.symbol}/{fill.order_id}."))
    for event in state.events:
        if event.event_id not in known_event_ids:
            issues.append(
                DriftIssue("unlinked_broker_event", event.event_id, f"Unknown {event.event_type} event for {event.symbol}.")
            )
    return tuple(issues)


def require_no_broker_state_drift(database: CioDatabase, state: BrokerStateSnapshot) -> None:
    issues = detect_broker_state_drift(database, state)
    database.save_broker_state_snapshot(state.observed_at, state, issues)
    if issues:
        summary = "; ".join(f"{item.kind}:{item.identifier}" for item in issues)
        raise PolicyViolation(f"Broker state drift requires reconciliation before recommendations: {summary}.")


@dataclass(frozen=True)
class WatchdogResult:
    run_key: str
    state: str
    detail: str

    @property
    def alert_required(self) -> bool:
        return self.state == "missed"


def check_daily_run_watchdog(
    database: CioDatabase,
    *,
    account_label: str,
    scheduled_for: datetime,
    now: datetime | None = None,
    grace: timedelta = timedelta(minutes=15),
) -> WatchdogResult:
    current = now or datetime.now(scheduled_for.tzinfo or timezone.utc)
    if scheduled_for.tzinfo is None or current.tzinfo is None:
        raise PolicyViolation("Watchdog timestamps must include a timezone.")
    run_key = f"{scheduled_for.date().isoformat()}:{account_label}:daily-review"
    if current < scheduled_for + grace:
        return WatchdogResult(run_key, "not_due", "Daily review is still within its completion grace period.")
    run = database.daily_run(run_key)
    if run and run["status"] == "completed":
        return WatchdogResult(run_key, "healthy", f"Daily review completed at {run['completed_at']}.")
    status = "missing" if run is None else str(run["status"])
    return WatchdogResult(run_key, "missed", f"Daily review is not complete after the grace period (status={status}).")


def changed_since_yesterday(
    current: Mapping[str, Any], previous: Mapping[str, Any] | None, *, keys: tuple[str, ...]
) -> tuple[str, ...]:
    if previous is None:
        return ("First recorded review; no prior daily state is available.",)
    changes = []
    for key in keys:
        before = previous.get(key)
        after = current.get(key)
        if before != after:
            changes.append(f"{key.replace('_', ' ').title()}: {before!s} → {after!s}")
    return tuple(changes) or ("No material changes since the previous review.",)


@dataclass(frozen=True)
class DailyNotice:
    action: str
    what_to_do: str
    why: str
    next_review: str
    live_trading_enabled: bool
    changes: tuple[str, ...]
    data_as_of: Mapping[str, str | None]
    approval_id: str | None = None
    order_fingerprint: str | None = None
    watchlist: tuple[str, ...] = ()


def render_daily_notice(notice: DailyNotice) -> str:
    lines = [
        f"*ACTION: {notice.action}*",
        f"*WHAT YOU SHOULD DO:* {notice.what_to_do}",
        f"*WHY:* {notice.why}",
        f"*NEXT REVIEW:* {notice.next_review}",
        f"*LIVE TRADING:* {'Enabled' if notice.live_trading_enabled else 'Disabled'}",
        "",
        "*CHANGED SINCE YESTERDAY*",
        *(f"• {item}" for item in notice.changes),
        "",
        "*DATA AS OF*",
        *(f"• {name}: {observed_at or 'missing'}" for name, observed_at in sorted(notice.data_as_of.items())),
    ]
    if notice.watchlist:
        lines.extend(("", "*WATCHLIST ONLY — NOT A BUY RECOMMENDATION*"))
        lines.extend(f"• {item}" for item in notice.watchlist)
    lines.extend((
        "",
        f"*Approval ID:* {notice.approval_id or 'None — no order exists'}",
        f"*Order fingerprint:* {notice.order_fingerprint or 'None — no order exists'}",
        "Slack cannot approve execution. Any live placement requires a fresh broker review and matching Codex approval.",
    ))
    return "\n".join(lines)
