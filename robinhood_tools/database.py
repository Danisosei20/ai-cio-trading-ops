from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .approvals import ApprovalRecord, order_fingerprint
from .errors import PolicyViolation
from .governance import DecisionRecord
from .models import EquityOrderRequest, OrderReview
from .research import ResearchExperiment, ResearchRun


DATABASE_SCHEMA_VERSION = 5


class CioDatabase:
    """Transactional approval, audit, exit-plan, delivery, and learning store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
                    order_fingerprint TEXT NOT NULL, account_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    status TEXT NOT NULL, approved_at TEXT, executed_at TEXT,
                    broker_review TEXT, order_id TEXT, failure TEXT,
                    CHECK(status IN ('pending','approved','executing','executed','failed','expired','rejected','reconciliation_required'))
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, correlation_id TEXT NOT NULL,
                    approval_id TEXT, event TEXT NOT NULL, created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exit_plans (
                    symbol TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_checkpoints (
                    recommendation_id TEXT NOT NULL, trading_day INTEGER NOT NULL,
                    due_date TEXT NOT NULL, completed_at TEXT, payload TEXT,
                    PRIMARY KEY(recommendation_id, trading_day)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL, status TEXT NOT NULL, attempted_at TEXT NOT NULL,
                    message_ts TEXT, message_link TEXT, retry_count INTEGER NOT NULL, error TEXT
                );
                CREATE TABLE IF NOT EXISTS daily_runs (
                    run_key TEXT PRIMARY KEY, status TEXT NOT NULL,
                    started_at TEXT NOT NULL, completed_at TEXT, detail TEXT
                );
                CREATE TABLE IF NOT EXISTS daily_run_checkpoints (
                    run_key TEXT NOT NULL, step TEXT NOT NULL, status TEXT NOT NULL,
                    updated_at TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(run_key, step)
                );
                CREATE TABLE IF NOT EXISTS processed_slack_messages (
                    channel_id TEXT NOT NULL, message_ts TEXT NOT NULL,
                    processed_at TEXT NOT NULL, command_kind TEXT NOT NULL,
                    PRIMARY KEY(channel_id, message_ts)
                );
                CREATE TABLE IF NOT EXISTS slack_reply_windows (
                    approval_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,
                    parent_message_ts TEXT, opened_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    status TEXT NOT NULL, resolved_at TEXT,
                    CHECK(status IN ('open','responded','rejected','executed','cancelled'))
                );
                CREATE TABLE IF NOT EXISTS trade_lifecycles (
                    symbol TEXT PRIMARY KEY, task_name TEXT NOT NULL,
                    buy_approval_id TEXT, buy_order_id TEXT,
                    sell_approval_id TEXT, sell_order_id TEXT,
                    status TEXT NOT NULL, opened_at TEXT NOT NULL,
                    closed_at TEXT, realized_profit TEXT,
                    CHECK(status IN ('research','buy_pending','open','sell_pending','closed','rejected'))
                );
                CREATE TABLE IF NOT EXISTS lifecycle_leases (
                    symbol TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_fills (
                    order_id TEXT NOT NULL, fill_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, side TEXT NOT NULL,
                    quantity TEXT NOT NULL, price TEXT NOT NULL,
                    fee TEXT NOT NULL, filled_at TEXT NOT NULL,
                    PRIMARY KEY(order_id, fill_id)
                );
                CREATE TABLE IF NOT EXISTS strategy_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recommendation_id TEXT NOT NULL, symbol TEXT NOT NULL,
                    strategy_version TEXT NOT NULL, market_regime TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL, expected_return TEXT,
                    actual_return TEXT, benchmark_return TEXT,
                    max_favorable_excursion TEXT, max_adverse_excursion TEXT,
                    thesis_accurate INTEGER, error_category TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dashboard_snapshots (
                    account_label TEXT PRIMARY KEY, observed_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS system_controls (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS symbol_cooldowns (
                    symbol TEXT PRIMARY KEY, reason TEXT NOT NULL,
                    starts_on TEXT NOT NULL, expires_on TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shadow_equity_recommendations (
                    recommendation_id TEXT PRIMARY KEY, run_key TEXT NOT NULL UNIQUE,
                    symbol TEXT, score INTEGER, action TEXT NOT NULL, market_regime TEXT NOT NULL,
                    observed_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_review_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, account_label TEXT NOT NULL,
                    observed_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS freshness_manifests (
                    run_key TEXT PRIMARY KEY, generated_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS broker_state_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, observed_at TEXT NOT NULL,
                    payload TEXT NOT NULL, drift_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS broker_events (
                    event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, event_type TEXT NOT NULL,
                    observed_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS health_alerts (
                    alert_key TEXT PRIMARY KEY, status TEXT NOT NULL, attempted_at TEXT NOT NULL,
                    completed_at TEXT, error TEXT
                );
                CREATE TABLE IF NOT EXISTS decision_records (
                    decision_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    recommendation TEXT NOT NULL, score INTEGER NOT NULL,
                    model_name TEXT NOT NULL, model_version TEXT NOT NULL,
                    policy_version TEXT NOT NULL, payload TEXT NOT NULL,
                    CHECK(recommendation IN ('hold','add','trim','sell','no_action')),
                    CHECK(score BETWEEN 0 AND 100)
                );
                CREATE TABLE IF NOT EXISTS research_experiments (
                    experiment_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    status TEXT NOT NULL, strategy_version TEXT NOT NULL,
                    baseline_version TEXT NOT NULL, minimum_observations INTEGER NOT NULL,
                    replay_snapshot_sha256 TEXT NOT NULL, approved_by TEXT,
                    status_updated_at TEXT NOT NULL, payload TEXT NOT NULL,
                    CHECK(status IN ('proposed','paper','accepted','rejected','rolled_back')),
                    CHECK(minimum_observations >= 10)
                );
                CREATE TABLE IF NOT EXISTS research_experiment_runs (
                    run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL, market_regime TEXT NOT NULL,
                    observation_count INTEGER NOT NULL, payload TEXT NOT NULL,
                    FOREIGN KEY(experiment_id) REFERENCES research_experiments(experiment_id),
                    CHECK(market_regime IN ('risk_on','neutral','risk_off')),
                    CHECK(observation_count > 0)
                );
                """
            )
            if db.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 0:
                db.execute("INSERT INTO schema_version VALUES (?)", (DATABASE_SCHEMA_VERSION,))
            db.execute(
                "INSERT OR IGNORE INTO system_controls VALUES('emergency_kill','off',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(processed_slack_messages)")}
            if "acknowledged_at" not in columns:
                db.execute("ALTER TABLE processed_slack_messages ADD COLUMN acknowledged_at TEXT")

    def integrity_check(self) -> str:
        with self.connect() as db:
            return str(db.execute("PRAGMA integrity_check").fetchone()[0])

    def backup(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(target) as backup:
            source.backup(backup)
        return target

    def claim_daily_run(self, run_key: str, *, stale_after: timedelta = timedelta(minutes=30)) -> bool:
        now = datetime.now(timezone.utc)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,started_at FROM daily_runs WHERE run_key=?", (run_key,)).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO daily_runs(run_key,status,started_at) VALUES(?,?,?)",
                    (run_key, "running", now.isoformat()),
                )
                db.execute("COMMIT")
                return True
            started_at = datetime.fromisoformat(row["started_at"])
            if row["status"] == "running" and now - started_at >= stale_after:
                db.execute(
                    "UPDATE daily_runs SET started_at=?,completed_at=NULL,detail=? WHERE run_key=?",
                    (now.isoformat(), "Recovered stale daily run.", run_key),
                )
                db.execute("COMMIT")
                return True
            db.execute("ROLLBACK")
            return False

    def complete_daily_run(self, run_key: str, status: str, detail: str = "") -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE daily_runs SET status=?, completed_at=?, detail=? WHERE run_key=?",
                (status, datetime.now(timezone.utc).isoformat(), detail, run_key),
            )

    def daily_run(self, run_key: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM daily_runs WHERE run_key=?", (run_key,)).fetchone()
        return dict(row) if row else None

    def stale_daily_runs(self, *, stale_after: timedelta = timedelta(minutes=30)) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - stale_after).isoformat()
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM daily_runs WHERE status='running' AND started_at<=? ORDER BY started_at", (cutoff,)
            ).fetchall()
        return [dict(row) for row in rows]

    def save_run_checkpoint(self, run_key: str, step: str, status: str, payload: dict) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO daily_run_checkpoints VALUES(?,?,?,?,?)",
                (run_key, step, status, datetime.now(timezone.utc).isoformat(), self._json(payload)),
            )

    def run_checkpoint(self, run_key: str, step: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM daily_run_checkpoints WHERE run_key=? AND step=?", (run_key, step)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def claim_slack_message(self, channel_id: str, message_ts: str, command_kind: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO processed_slack_messages(channel_id,message_ts,processed_at,command_kind) VALUES(?,?,?,?)",
                    (channel_id, message_ts, datetime.now(timezone.utc).isoformat(), command_kind),
                )
                db.execute("COMMIT")
                return True
            except sqlite3.IntegrityError:
                row = db.execute(
                    "SELECT acknowledged_at,processed_at FROM processed_slack_messages WHERE channel_id=? AND message_ts=?",
                    (channel_id, message_ts),
                ).fetchone()
                if row["acknowledged_at"] is not None:
                    db.execute("ROLLBACK")
                    return False
                last = datetime.fromisoformat(row["processed_at"])
                if datetime.now(timezone.utc) - last < timedelta(seconds=30):
                    db.execute("ROLLBACK")
                    return False
                db.execute(
                    "UPDATE processed_slack_messages SET processed_at=?,command_kind=? WHERE channel_id=? AND message_ts=?",
                    (datetime.now(timezone.utc).isoformat(), command_kind, channel_id, message_ts),
                )
                db.execute("COMMIT")
                return True

    def mark_slack_message_acknowledged(self, channel_id: str, message_ts: str) -> None:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE processed_slack_messages SET acknowledged_at=? WHERE channel_id=? AND message_ts=?",
                (datetime.now(timezone.utc).isoformat(), channel_id, message_ts),
            ).rowcount
            if changed != 1:
                raise PolicyViolation("Slack message claim was not found.")

    def open_reply_window(
        self, approval_id: str, channel_id: str, parent_message_ts: str | None, *, minutes: int = 10
    ) -> None:
        now = datetime.now(timezone.utc)
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO slack_reply_windows VALUES(?,?,?,?,?,'open',NULL)",
                (approval_id, channel_id, parent_message_ts, now.isoformat(), (now + timedelta(minutes=minutes)).isoformat()),
            )

    def resolve_reply_window(self, approval_id: str, status: str) -> None:
        if status not in {"responded", "rejected", "executed", "cancelled"}:
            raise PolicyViolation("Invalid reply-window terminal status.")
        with self.connect() as db:
            db.execute(
                "UPDATE slack_reply_windows SET status=?,resolved_at=? WHERE approval_id=? AND status='open'",
                (status, datetime.now(timezone.utc).isoformat(), approval_id),
            )

    def open_reply_windows(self, now: datetime | None = None) -> list[dict]:
        current = (now or datetime.now(timezone.utc)).isoformat()
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM slack_reply_windows WHERE status='open' AND expires_at>? ORDER BY opened_at",
                (current,),
            ).fetchall()
        return [dict(row) for row in rows]

    def reject_expired_reply_windows(self, now: datetime | None = None) -> list[str]:
        current = (now or datetime.now(timezone.utc)).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT approval_id FROM slack_reply_windows WHERE status='open' AND expires_at<=?", (current,)
            ).fetchall()
            approval_ids = [row["approval_id"] for row in rows]
            for approval_id in approval_ids:
                db.execute(
                    "UPDATE approvals SET status='rejected' WHERE approval_id=? AND status IN ('pending','approved')",
                    (approval_id,),
                )
                db.execute(
                    "UPDATE slack_reply_windows SET status='rejected',resolved_at=? WHERE approval_id=?",
                    (current, approval_id),
                )
            db.execute("COMMIT")
        return approval_ids

    def cleanup_terminal_reply_windows(self) -> int:
        with self.connect() as db:
            return db.execute(
                "DELETE FROM slack_reply_windows WHERE status IN ('rejected','executed','cancelled')"
            ).rowcount

    def list_approvals(self, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT approval_id,symbol,status,created_at,expires_at,order_id,failure FROM approvals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_reconciliation_required(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT approval_id,account_id,symbol,order_id,failure,created_at "
                "FROM approvals WHERE status='reconciliation_required' ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def record_delivery(self, *, approval_id: str, channel_id: str, status: str, attempted_at: str,
                        message_ts=None, message_link=None, retry_count=0, error=None):
        with self.connect() as db:
            db.execute(
                "INSERT INTO deliveries(approval_id,channel_id,status,attempted_at,message_ts,message_link,retry_count,error) VALUES(?,?,?,?,?,?,?,?)",
                (approval_id, channel_id, status, attempted_at, message_ts, message_link, retry_count, error),
            )

    def create(self, request: EquityOrderRequest, review: OrderReview, *, window_minutes: int, approval_id=None):
        now = datetime.now(timezone.utc)
        record = ApprovalRecord(
            approval_id=approval_id or str(uuid.uuid4()), review_id=review.review_id,
            order_fingerprint=order_fingerprint(request), account_id=request.account_id,
            symbol=request.symbol.upper(), created_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=window_minutes)).isoformat(), broker_review=review.raw,
        )
        with self.connect() as db:
            try:
                db.execute(
                    "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (record.approval_id, record.review_id, record.order_fingerprint, record.account_id,
                     record.symbol, record.created_at, record.expires_at, record.status, None, None,
                     json.dumps(record.broker_review), None, None),
                )
            except sqlite3.IntegrityError as exc:
                raise PolicyViolation("Approval ID already exists.") from exc
        return record

    def approve(self, approval_id: str):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, approval_id)
            self._expire(db, row)
            changed = db.execute(
                "UPDATE approvals SET status='approved', approved_at=? WHERE approval_id=? AND status='pending'",
                (now, approval_id),
            ).rowcount
            if changed != 1:
                raise PolicyViolation(f"Approval {approval_id!r} is not pending.")
            db.execute("COMMIT")
        return self.get(approval_id)

    def reject(self, approval_id: str):
        with self.connect() as db:
            changed = db.execute(
                "UPDATE approvals SET status='rejected' WHERE approval_id=? AND status IN ('pending','approved')",
                (approval_id,),
            ).rowcount
            if changed != 1:
                raise PolicyViolation("Only a pending or approved, unexecuted request can be rejected.")
        return self.get(approval_id)

    def require_for_placement(self, approval_id: str, request: EquityOrderRequest, review_id: str):
        with self.connect() as db:
            row = self._row(db, approval_id)
            self._expire(db, row)
            if row["status"] != "approved":
                raise PolicyViolation(f"Approval {approval_id!r} is {row['status']!r}, not approved.")
            if row["review_id"] != review_id or row["order_fingerprint"] != order_fingerprint(request):
                raise PolicyViolation("Approval does not match the exact reviewed order.")
        return self.get(approval_id)

    def reserve_execution(self, approval_id: str, request: EquityOrderRequest, review_id: str):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, approval_id)
            self._expire(db, row)
            if row["review_id"] != review_id or row["order_fingerprint"] != order_fingerprint(request):
                raise PolicyViolation("Approval does not match the exact reviewed order.")
            changed = db.execute(
                "UPDATE approvals SET status='executing' WHERE approval_id=? AND status='approved'",
                (approval_id,),
            ).rowcount
            if changed != 1:
                raise PolicyViolation(f"Approval {approval_id!r} cannot be reserved from status {row['status']!r}.")
            db.execute("COMMIT")

    def mark_executed(self, approval_id: str, order_id: str | None = None):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            changed = db.execute(
                "UPDATE approvals SET status='executed', executed_at=?, order_id=? WHERE approval_id=? AND status='executing'",
                (now, order_id, approval_id),
            ).rowcount
            if changed != 1:
                raise PolicyViolation("Approval was not reserved for execution.")
        return self.get(approval_id)

    def mark_reconciliation_required(self, approval_id: str, error: str):
        with self.connect() as db:
            db.execute(
                "UPDATE approvals SET status='reconciliation_required', failure=? WHERE approval_id=? AND status='executing'",
                (error, approval_id),
            )

    def get(self, approval_id: str) -> ApprovalRecord:
        with self.connect() as db:
            row = self._row(db, approval_id)
        return ApprovalRecord(
            approval_id=row["approval_id"], review_id=row["review_id"],
            order_fingerprint=row["order_fingerprint"], account_id=row["account_id"],
            symbol=row["symbol"], created_at=row["created_at"], expires_at=row["expires_at"],
            status=row["status"], approved_at=row["approved_at"], executed_at=row["executed_at"],
            broker_review=json.loads(row["broker_review"] or "null"),
        )

    def audit(self, event: str, payload: dict, *, correlation_id: str, approval_id: str | None = None):
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit_events(correlation_id,approval_id,event,created_at,payload) VALUES(?,?,?,?,?)",
                (correlation_id, approval_id, event, datetime.now(timezone.utc).isoformat(), json.dumps(payload, default=str)),
            )

    def save_exit_plan(self, symbol: str, payload: dict):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO exit_plans VALUES(?,?,?)",
                (symbol.upper(), datetime.now(timezone.utc).isoformat(), json.dumps(payload, default=str)),
            )

    def schedule_learning(self, recommendation_id: str, due_dates: dict[int, str]):
        with self.connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO learning_checkpoints(recommendation_id,trading_day,due_date) VALUES(?,?,?)",
                [(recommendation_id, day, due) for day, due in due_dates.items()],
            )

    def record_shadow_recommendation(
        self, *, recommendation_id: str, run_key: str, symbol: str | None, score: int | None,
        action: str, market_regime: str, payload: dict,
    ) -> dict:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO shadow_equity_recommendations VALUES(?,?,?,?,?,?,?,?)",
                (recommendation_id, run_key, symbol.upper() if symbol else None, score, action,
                 market_regime, datetime.now(timezone.utc).isoformat(), self._json(payload)),
            )
            row = db.execute("SELECT * FROM shadow_equity_recommendations WHERE run_key=?", (run_key,)).fetchone()
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def shadow_recommendations(self, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM shadow_equity_recommendations ORDER BY observed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def save_daily_review_state(self, account_label: str, payload: dict) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO daily_review_states(account_label,observed_at,payload) VALUES(?,?,?)",
                (account_label, datetime.now(timezone.utc).isoformat(), self._json(payload)),
            )

    def previous_daily_review_state(self, account_label: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM daily_review_states WHERE account_label=? ORDER BY observed_at DESC LIMIT 1",
                (account_label,),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_freshness_manifest(self, run_key: str, generated_at: str, payload: dict) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO freshness_manifests VALUES(?,?,?)",
                (run_key, generated_at, self._json(payload)),
            )

    def begin_trade_lifecycle(self, symbol: str, *, buy_approval_id: str | None = None) -> dict:
        normalized = symbol.upper()
        now = datetime.now(timezone.utc).isoformat()
        status = "buy_pending" if buy_approval_id else "research"
        with self.connect() as db:
            try:
                db.execute(
                    "INSERT INTO trade_lifecycles(symbol,task_name,buy_approval_id,status,opened_at) VALUES(?,?,?,?,?)",
                    (normalized, normalized, buy_approval_id, status, now),
                )
            except sqlite3.IntegrityError as exc:
                raise PolicyViolation(
                    f"{normalized} already has a lifecycle task; continue that same task instead of creating another."
                ) from exc
        return self.get_trade_lifecycle(normalized)

    def get_trade_lifecycle(self, symbol: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM trade_lifecycles WHERE symbol=?", (symbol.upper(),)).fetchone()
        if not row:
            raise PolicyViolation(f"No lifecycle task exists for {symbol.upper()}.")
        return dict(row)

    def mark_position_open(self, symbol: str, *, order_id: str) -> dict:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE trade_lifecycles SET status='open',buy_order_id=? WHERE symbol=? AND status='buy_pending'",
                (order_id, symbol.upper()),
            ).rowcount
        if changed != 1:
            raise PolicyViolation("Only a buy-pending lifecycle can become an open position.")
        return self.get_trade_lifecycle(symbol)

    def attach_buy_authorization(self, symbol: str, *, approval_id: str) -> dict:
        """Attach a reviewed buy to an existing research lifecycle without creating a second ticker task."""
        with self.connect() as db:
            changed = db.execute(
                "UPDATE trade_lifecycles SET status='buy_pending',buy_approval_id=? "
                "WHERE symbol=? AND status='research'",
                (approval_id, symbol.upper()),
            ).rowcount
        if changed != 1:
            raise PolicyViolation("Only a research lifecycle can receive a buy authorization.")
        return self.get_trade_lifecycle(symbol)

    def mark_sell_pending(self, symbol: str, *, approval_id: str) -> dict:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE trade_lifecycles SET status='sell_pending',sell_approval_id=? WHERE symbol=? AND status='open'",
                (approval_id, symbol.upper()),
            ).rowcount
        if changed != 1:
            raise PolicyViolation("Only an open position can begin a sell review.")
        return self.get_trade_lifecycle(symbol)

    def close_trade_lifecycle(self, symbol: str, *, order_id: str, realized_profit: str) -> dict:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE trade_lifecycles SET status='closed',sell_order_id=?,realized_profit=?,closed_at=? "
                "WHERE symbol=? AND status='sell_pending'",
                (order_id, realized_profit, datetime.now(timezone.utc).isoformat(), symbol.upper()),
            ).rowcount
        if changed != 1:
            raise PolicyViolation("Only a sell-pending lifecycle can be closed.")
        return self.get_trade_lifecycle(symbol)

    def list_trade_lifecycles(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM trade_lifecycles ORDER BY opened_at DESC").fetchall()
        return [dict(row) for row in rows]

    def acquire_lifecycle_lease(self, symbol: str, owner: str, *, seconds: int = 300) -> bool:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=seconds)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM lifecycle_leases WHERE symbol=? AND expires_at<=?", (symbol.upper(), now.isoformat()))
            try:
                db.execute(
                    "INSERT INTO lifecycle_leases VALUES(?,?,?,?)",
                    (symbol.upper(), owner, now.isoformat(), expires.isoformat()),
                )
                db.execute("COMMIT")
                return True
            except sqlite3.IntegrityError:
                db.execute("ROLLBACK")
                return False

    def release_lifecycle_lease(self, symbol: str, owner: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM lifecycle_leases WHERE symbol=? AND owner=?", (symbol.upper(), owner))

    def record_fill(self, *, order_id: str, fill_id: str, symbol: str, side: str,
                    quantity: str, price: str, fee: str, filled_at: str) -> bool:
        with self.connect() as db:
            changed = db.execute(
                "INSERT OR IGNORE INTO order_fills VALUES(?,?,?,?,?,?,?,?)",
                (order_id, fill_id, symbol.upper(), side, quantity, price, fee, filled_at),
            ).rowcount
        return changed == 1

    def order_fills(self, order_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM order_fills WHERE order_id=? ORDER BY filled_at", (order_id,)).fetchall()
        return [dict(row) for row in rows]

    def expected_open_symbols(self) -> set[str]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT symbol FROM trade_lifecycles WHERE status IN ('open','sell_pending')"
            ).fetchall()
        return {str(row["symbol"]).upper() for row in rows}

    def known_order_ids(self) -> set[str]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT order_id FROM approvals WHERE order_id IS NOT NULL "
                "UNION SELECT buy_order_id FROM trade_lifecycles WHERE buy_order_id IS NOT NULL "
                "UNION SELECT sell_order_id FROM trade_lifecycles WHERE sell_order_id IS NOT NULL"
            ).fetchall()
        return {str(row[0]) for row in rows}

    def known_fill_ids(self) -> set[str]:
        with self.connect() as db:
            rows = db.execute("SELECT fill_id FROM order_fills").fetchall()
        return {str(row[0]) for row in rows}

    def record_broker_event(
        self, *, event_id: str, symbol: str, event_type: str, observed_at: str, payload: dict,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO broker_events VALUES(?,?,?,?,?)",
                (event_id, symbol.upper(), event_type, observed_at, self._json(payload)),
            )

    def known_broker_event_ids(self) -> set[str]:
        with self.connect() as db:
            rows = db.execute("SELECT event_id FROM broker_events").fetchall()
        return {str(row[0]) for row in rows}

    def save_broker_state_snapshot(self, observed_at: str, state, issues) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO broker_state_snapshots(observed_at,payload,drift_count) VALUES(?,?,?)",
                (observed_at, self._json({"state": state, "issues": issues}), len(issues)),
            )

    def claim_health_alert(self, alert_key: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM health_alerts WHERE alert_key=?", (alert_key,)).fetchone()
            if row and row["status"] == "sent":
                db.execute("ROLLBACK")
                return False
            db.execute(
                "INSERT OR REPLACE INTO health_alerts(alert_key,status,attempted_at,completed_at,error) "
                "VALUES(?, 'pending', ?, NULL, NULL)",
                (alert_key, now),
            )
            db.execute("COMMIT")
        return True

    def complete_health_alert(self, alert_key: str, *, sent: bool, error: str | None = None) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE health_alerts SET status=?,completed_at=?,error=? WHERE alert_key=?",
                ("sent" if sent else "failed", datetime.now(timezone.utc).isoformat(), error, alert_key),
            )

    def record_strategy_observation(
        self, *, recommendation_id: str, symbol: str, strategy_version: str,
        market_regime: str, horizon_days: int, expected_return: str | None,
        actual_return: str | None, benchmark_return: str | None,
        max_favorable_excursion: str | None, max_adverse_excursion: str | None,
        thesis_accurate: bool | None, error_category: str | None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO strategy_observations(recommendation_id,symbol,strategy_version,market_regime,"
                "horizon_days,expected_return,actual_return,benchmark_return,max_favorable_excursion,"
                "max_adverse_excursion,thesis_accurate,error_category,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (recommendation_id, symbol.upper(), strategy_version, market_regime, horizon_days,
                 expected_return, actual_return, benchmark_return, max_favorable_excursion,
                 max_adverse_excursion, None if thesis_accurate is None else int(thesis_accurate),
                 error_category, datetime.now(timezone.utc).isoformat()),
            )

    def record_decision(self, record: DecisionRecord) -> str:
        """Persist immutable decision provenance; this does not create an approval."""
        payload = record.canonical_payload()
        decision_id = record.decision_id
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO decision_records VALUES(?,?,?,?,?,?,?,?)",
                (decision_id, record.created_at.isoformat(), record.recommendation, record.score,
                 record.model_name, record.model_version, record.policy_version, self._json(payload)),
            )
        return decision_id

    def decision_record(self, decision_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM decision_records WHERE decision_id=?", (decision_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def record_research_experiment(self, experiment: ResearchExperiment) -> str:
        payload = experiment.canonical_payload()
        experiment_id = experiment.experiment_id
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO research_experiments VALUES(?,?,?,?,?,?,?,?,?,?)",
                (experiment_id, experiment.created_at.isoformat(), "proposed", experiment.strategy_version,
                 experiment.baseline_version, experiment.minimum_observations,
                 experiment.replay_snapshot_sha256, None, now, self._json(payload)),
            )
        return experiment_id

    def research_experiment(self, experiment_id: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT *, (SELECT coalesce(sum(observation_count),0) FROM research_experiment_runs "
                "WHERE experiment_id=research_experiments.experiment_id) AS observations "
                "FROM research_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
        if not row:
            raise PolicyViolation(f"Research experiment {experiment_id!r} was not found.")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def list_research_experiments(self, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT e.experiment_id,e.created_at,e.status,e.strategy_version,e.baseline_version,"
                "e.minimum_observations,e.approved_by,coalesce(sum(r.observation_count),0) AS observations "
                "FROM research_experiments e LEFT JOIN research_experiment_runs r "
                "ON r.experiment_id=e.experiment_id GROUP BY e.experiment_id "
                "ORDER BY e.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_research_run(self, run: ResearchRun) -> str:
        payload = run.canonical_payload()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            experiment = db.execute(
                "SELECT status FROM research_experiments WHERE experiment_id=?", (run.experiment_id,)
            ).fetchone()
            if not experiment:
                db.execute("ROLLBACK")
                raise PolicyViolation("Research run references an unknown experiment.")
            if experiment["status"] != "paper":
                db.execute("ROLLBACK")
                raise PolicyViolation("Research runs may only be recorded while an experiment is in paper status.")
            db.execute(
                "INSERT OR IGNORE INTO research_experiment_runs VALUES(?,?,?,?,?,?)",
                (run.run_id, run.experiment_id, run.observed_at.isoformat(), run.market_regime,
                 run.observation_count, self._json(payload)),
            )
            db.execute("COMMIT")
        return run.run_id

    def transition_research_experiment(
        self, experiment_id: str, status: str, *, approved_by: str | None = None,
    ) -> dict:
        allowed = {
            "proposed": {"paper", "rejected"},
            "paper": {"accepted", "rejected"},
            "accepted": {"rolled_back"},
            "rejected": set(),
            "rolled_back": set(),
        }
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,minimum_observations FROM research_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
            if not row:
                db.execute("ROLLBACK")
                raise PolicyViolation(f"Research experiment {experiment_id!r} was not found.")
            if status not in allowed[row["status"]]:
                db.execute("ROLLBACK")
                raise PolicyViolation(f"Research experiment cannot transition from {row['status']} to {status}.")
            if status == "accepted":
                observations = int(db.execute(
                    "SELECT coalesce(sum(observation_count),0) FROM research_experiment_runs WHERE experiment_id=?",
                    (experiment_id,),
                ).fetchone()[0])
                if observations < int(row["minimum_observations"]):
                    db.execute("ROLLBACK")
                    raise PolicyViolation(
                        f"Research acceptance requires {row['minimum_observations']} paper observations; "
                        f"found {observations}."
                    )
                if not approved_by or not approved_by.strip():
                    db.execute("ROLLBACK")
                    raise PolicyViolation("Research acceptance requires a recorded human approver.")
            db.execute(
                "UPDATE research_experiments SET status=?,approved_by=?,status_updated_at=? WHERE experiment_id=?",
                (status, approved_by.strip() if approved_by else None,
                 datetime.now(timezone.utc).isoformat(), experiment_id),
            )
            db.execute("COMMIT")
        return self.research_experiment(experiment_id)

    def strategy_observation_count(self, *, strategy_version: str, error_category: str) -> int:
        with self.connect() as db:
            return int(db.execute(
                "SELECT count(*) FROM strategy_observations WHERE strategy_version=? AND error_category=?",
                (strategy_version, error_category),
            ).fetchone()[0])

    def save_dashboard_snapshot(self, account_label: str, payload: dict) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO dashboard_snapshots VALUES(?,?,?)",
                (account_label, datetime.now(timezone.utc).isoformat(), json.dumps(payload, default=str)),
            )

    def dashboard_snapshots(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM dashboard_snapshots ORDER BY account_label").fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def set_emergency_kill(self, enabled: bool) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO system_controls VALUES('emergency_kill',?,?)",
                ("on" if enabled else "off", datetime.now(timezone.utc).isoformat()),
            )

    def require_not_killed(self) -> None:
        with self.connect() as db:
            row = db.execute("SELECT value FROM system_controls WHERE key='emergency_kill'").fetchone()
        if row and row["value"] == "on":
            raise PolicyViolation("Emergency kill switch is active; new reviews and placements are disabled.")

    def add_symbol_cooldown(self, symbol: str, *, reason: str, starts_on: str, expires_on: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO symbol_cooldowns VALUES(?,?,?,?)",
                (symbol.upper(), reason, starts_on, expires_on),
            )

    def require_no_symbol_cooldown(self, symbol: str, *, today: str) -> None:
        with self.connect() as db:
            row = db.execute(
                "SELECT reason,expires_on FROM symbol_cooldowns WHERE symbol=? AND expires_on>=?",
                (symbol.upper(), today),
            ).fetchone()
        if row:
            raise PolicyViolation(f"{symbol.upper()} is in cooldown through {row['expires_on']}: {row['reason']}")

    def dashboard(self) -> dict:
        with self.connect() as db:
            statuses = {row["status"]: row["count"] for row in db.execute(
                "SELECT status, count(*) AS count FROM approvals GROUP BY status"
            )}
            overdue = db.execute(
                "SELECT count(*) FROM learning_checkpoints WHERE completed_at IS NULL AND due_date < ?",
                (datetime.now(timezone.utc).date().isoformat(),),
            ).fetchone()[0]
            leases = db.execute("SELECT count(*) FROM lifecycle_leases WHERE expires_at>?", (datetime.now(timezone.utc).isoformat(),)).fetchone()[0]
            reconciliation = statuses.get("reconciliation_required", 0)
            failed_deliveries = db.execute("SELECT count(*) FROM deliveries WHERE status='failed'").fetchone()[0]
            shadow_count = db.execute("SELECT count(*) FROM shadow_equity_recommendations").fetchone()[0]
            drift_count = db.execute("SELECT coalesce(sum(drift_count),0) FROM broker_state_snapshots").fetchone()[0]
            experiment_statuses = {row["status"]: row["count"] for row in db.execute(
                "SELECT status, count(*) AS count FROM research_experiments GROUP BY status"
            )}
            return {"approval_statuses": statuses, "overdue_learning_checkpoints": overdue,
                    "active_leases": leases, "reconciliation_required": reconciliation,
                    "failed_slack_deliveries": failed_deliveries,
                    "shadow_recommendations": shadow_count,
                    "research_experiment_statuses": experiment_statuses,
                    "broker_drift_findings": drift_count,
                    "portfolio_snapshots": self.dashboard_snapshots(),
                    "emergency_kill": db.execute(
                        "SELECT value FROM system_controls WHERE key='emergency_kill'"
                    ).fetchone()[0],
                    "active_cooldowns": db.execute(
                        "SELECT count(*) FROM symbol_cooldowns WHERE expires_on>=?",
                        (datetime.now(timezone.utc).date().isoformat(),),
                    ).fetchone()[0]}

    def audit_export(self) -> dict:
        tables = ("approvals", "audit_events", "exit_plans", "learning_checkpoints", "deliveries",
                  "daily_runs", "processed_slack_messages", "slack_reply_windows", "trade_lifecycles",
                  "order_fills", "strategy_observations", "dashboard_snapshots", "symbol_cooldowns",
                  "daily_run_checkpoints", "shadow_equity_recommendations", "daily_review_states",
                  "freshness_manifests", "broker_state_snapshots", "broker_events", "health_alerts",
                  "decision_records", "research_experiments", "research_experiment_runs")
        with self.connect() as db:
            return {table: [dict(row) for row in db.execute(f"SELECT * FROM {table}")] for table in tables}

    def _row(self, db, approval_id):
        row = db.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if not row:
            raise PolicyViolation(f"Approval {approval_id!r} was not found.")
        return row

    def _expire(self, db, row):
        if row["status"] in {"pending", "approved"} and datetime.now(timezone.utc) >= datetime.fromisoformat(row["expires_at"]):
            db.execute("UPDATE approvals SET status='expired' WHERE approval_id=?", (row["approval_id"],))
            raise PolicyViolation("Approval expired; run a fresh broker review.")

    @staticmethod
    def _json(value) -> str:
        def default(item):
            if is_dataclass(item):
                return asdict(item)  # type: ignore[arg-type]
            return str(item)

        return json.dumps(value, default=default, sort_keys=True)
