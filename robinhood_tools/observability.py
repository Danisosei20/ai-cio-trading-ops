from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from .database import CioDatabase


@dataclass(frozen=True)
class OperationalCheck:
    name: str
    state: str
    detail: str
    count: int = 0


@dataclass(frozen=True)
class OperationalStatus:
    state: str
    observed_at: str
    checks: tuple[OperationalCheck, ...]

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate_operational_status(
    database: CioDatabase, *, now: datetime | None = None, stale_after: timedelta = timedelta(minutes=30)
) -> OperationalStatus:
    """Summarize fail-closed operating state without contacting broker or Slack."""
    current = now or datetime.now(timezone.utc)
    checks: list[OperationalCheck] = []

    integrity = database.integrity_check()
    checks.append(OperationalCheck(
        "database_integrity", "ok" if integrity == "ok" else "critical", integrity,
        0 if integrity == "ok" else 1,
    ))

    with database.connect() as db:
        reconciliation = int(db.execute(
            "SELECT count(*) FROM approvals WHERE status='reconciliation_required'"
        ).fetchone()[0])
        stale_runs = int(db.execute(
            "SELECT count(*) FROM daily_runs WHERE status='running' AND started_at<=?",
            ((current - stale_after).isoformat(),),
        ).fetchone()[0])
        overdue = int(db.execute(
            "SELECT count(*) FROM learning_checkpoints WHERE completed_at IS NULL AND due_date<?",
            (current.date().isoformat(),),
        ).fetchone()[0])
        failed_deliveries = int(db.execute(
            "SELECT count(*) FROM deliveries WHERE status='failed'"
        ).fetchone()[0])
        failed_health = int(db.execute(
            "SELECT count(*) FROM health_alerts WHERE status='failed'"
        ).fetchone()[0])
        expired_windows = int(db.execute(
            "SELECT count(*) FROM slack_reply_windows WHERE status='open' AND expires_at<=?",
            (current.isoformat(),),
        ).fetchone()[0])
        latest_drift_row = db.execute(
            "SELECT drift_count FROM broker_state_snapshots ORDER BY observed_at DESC, id DESC LIMIT 1"
        ).fetchone()
        latest_drift = int(latest_drift_row[0]) if latest_drift_row else 0
        emergency_kill = str(db.execute(
            "SELECT value FROM system_controls WHERE key='emergency_kill'"
        ).fetchone()[0])

    for name, count, detail in (
        ("broker_reconciliation", reconciliation, "approvals require broker reconciliation"),
        ("stale_daily_runs", stale_runs, "daily runs exceeded the recovery threshold"),
        ("latest_broker_drift", latest_drift, "latest broker snapshot has unexplained drift"),
    ):
        checks.append(OperationalCheck(name, "critical" if count else "ok", detail, count))
    for name, count, detail in (
        ("overdue_learning", overdue, "learning checkpoints are overdue"),
        ("failed_slack_deliveries", failed_deliveries, "Slack deliveries failed"),
        ("failed_health_alerts", failed_health, "health alerts failed"),
        ("expired_reply_windows", expired_windows, "Slack reply windows need cleanup"),
    ):
        checks.append(OperationalCheck(name, "degraded" if count else "ok", detail, count))
    checks.append(OperationalCheck(
        "emergency_kill", "safe_stopped" if emergency_kill == "on" else "ok",
        f"emergency kill is {emergency_kill}", int(emergency_kill == "on"),
    ))

    if any(item.state == "critical" for item in checks):
        state = "critical"
    elif any(item.state == "degraded" for item in checks):
        state = "degraded"
    elif any(item.state == "safe_stopped" for item in checks):
        state = "safe_stopped"
    else:
        state = "healthy"
    return OperationalStatus(state, current.isoformat(), tuple(checks))
