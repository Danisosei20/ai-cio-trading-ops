from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from robinhood_tools.database import CioDatabase, DATABASE_SCHEMA_VERSION
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.governance import DecisionRecord, EvidenceTimestamp
from robinhood_tools.observability import evaluate_operational_status
from robinhood_tools.research import ResearchExperiment, ResearchMetric, ResearchRun
from robinhood_tools.replay import PointInTimeObservation, ReplaySnapshot
from scripts.restore_drill import run_restore_drill


DIGEST = "a" * 64


class GovernanceTests(unittest.TestCase):
    def test_decision_provenance_is_deterministic_and_persisted_without_approval(self):
        now = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
        evidence = (
            EvidenceTimestamp("quote", "broker", now - timedelta(seconds=5),
                              now - timedelta(seconds=10), DIGEST),
            EvidenceTimestamp("filing", "sec", now - timedelta(days=1),
                              now - timedelta(days=2), "d" * 64),
        )
        record = DecisionRecord(
            created_at=now, model_name="analyst", model_version="1", prompt_sha256=DIGEST,
            policy_version="4", policy_sha256="b" * 64, snapshot_sha256="c" * 64,
            recommendation="no_action", score=78, rationale="Position cap is already occupied.",
            evidence=evidence,
        )
        reordered = DecisionRecord(**{**record.__dict__, "evidence": tuple(reversed(evidence))})
        self.assertEqual(record.decision_id, reordered.decision_id)
        with tempfile.TemporaryDirectory() as directory:
            database = CioDatabase(Path(directory) / "cio.db")
            decision_id = database.record_decision(record)
            self.assertEqual(database.record_decision(record), decision_id)
            self.assertEqual(database.decision_record(decision_id)["recommendation"], "no_action")
            self.assertEqual(database.list_approvals(), [])

    def test_decision_rejects_future_evidence(self):
        now = datetime.now(timezone.utc)
        record = DecisionRecord(
            created_at=now, model_name="analyst", model_version="1", prompt_sha256=DIGEST,
            policy_version="4", policy_sha256=DIGEST, snapshot_sha256=DIGEST,
            recommendation="hold", score=90, rationale="test",
            evidence=(EvidenceTimestamp("future", "test", now + timedelta(seconds=1), now, DIGEST),),
        )
        with self.assertRaisesRegex(PolicyViolation, "after the decision"):
            _ = record.decision_id

    def test_decision_rejects_revised_data_not_effective_when_observed(self):
        now = datetime.now(timezone.utc)
        record = DecisionRecord(
            created_at=now, model_name="analyst", model_version="1", prompt_sha256=DIGEST,
            policy_version="4", policy_sha256=DIGEST, snapshot_sha256=DIGEST,
            recommendation="hold", score=90, rationale="test",
            evidence=(EvidenceTimestamp(
                "filing", "sec", now - timedelta(minutes=1), now + timedelta(minutes=1), DIGEST,
            ),),
        )
        with self.assertRaisesRegex(PolicyViolation, "not yet effective"):
            _ = record.decision_id


class ReplayTests(unittest.TestCase):
    def test_point_in_time_snapshot_has_stable_digest(self):
        now = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
        quote = PointInTimeObservation("quote", now, now, DIGEST)
        filing = PointInTimeObservation("filing", now - timedelta(days=1), now - timedelta(days=2), "b" * 64)
        first = ReplaySnapshot(now, (quote, filing), ("quote", "filing"))
        second = ReplaySnapshot(now, (filing, quote), ("filing", "quote"))
        self.assertEqual(first.digest, second.digest)

    def test_point_in_time_snapshot_blocks_look_ahead_bias(self):
        now = datetime.now(timezone.utc)
        snapshot = ReplaySnapshot(
            now,
            (PointInTimeObservation("earnings", now, now + timedelta(days=1), DIGEST),),
            ("earnings",),
        )
        with self.assertRaisesRegex(PolicyViolation, "unavailable at decision time"):
            _ = snapshot.digest


class RecoveryAndObservabilityTests(unittest.TestCase):
    def test_restore_drill_is_non_destructive_and_checks_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cio.db"
            CioDatabase(path)
            result = run_restore_drill(path)
            self.assertTrue(result["passed"])
            self.assertTrue(result["source_unchanged"])
            self.assertEqual(result["schema_version"], DATABASE_SCHEMA_VERSION)

    def test_restore_drill_rejects_wrong_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cio.db"
            CioDatabase(path)
            result = run_restore_drill(path, expected_schema=DATABASE_SCHEMA_VERSION + 1)
            self.assertFalse(result["passed"])

    def test_operational_status_prioritizes_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = CioDatabase(Path(directory) / "cio.db")
            with database.connect() as db:
                db.execute(
                    "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("approval", "review", DIGEST, "account", "AAPL", "2026-07-13T12:00:00+00:00",
                     "2026-07-13T12:10:00+00:00", "reconciliation_required", None, None, None, None,
                     "broker result unknown"),
                )
            status = evaluate_operational_status(
                database, now=datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
            )
            self.assertEqual(status.state, "critical")
            self.assertEqual(
                next(item.count for item in status.checks if item.name == "broker_reconciliation"), 1
            )

    def test_operational_status_reports_emergency_stop_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            database = CioDatabase(Path(directory) / "cio.db")
            database.set_emergency_kill(True)
            status = evaluate_operational_status(database)
            self.assertEqual(status.state, "safe_stopped")


class ResearchGovernanceTests(unittest.TestCase):
    def build_experiment(self, now: datetime) -> ResearchExperiment:
        return ResearchExperiment(
            created_at=now, name="opening confirmation", hypothesis="Later confirmation reduces false entries.",
            strategy_version="v2", baseline_version="v1", code_commit="abcdef1",
            policy_sha256=DIGEST, parameter_sha256="b" * 64, replay_snapshot_sha256="c" * 64,
            expected_benefit="Improve excess return after costs.",
            rollback_criteria="Roll back if drawdown or slippage is worse than baseline.",
        )

    def build_run(self, experiment_id: str, now: datetime, count: int, suffix: str) -> ResearchRun:
        return ResearchRun(
            experiment_id=experiment_id, observed_at=now, market_regime="neutral",
            observation_count=count, dataset_sha256=suffix * 64, artifact_sha256="e" * 64,
            metrics=(
                ResearchMetric("excess_return", Decimal("0.01")),
                ResearchMetric("max_drawdown", Decimal("-0.02")),
                ResearchMetric("turnover", Decimal("0.10")),
                ResearchMetric("slippage", Decimal("0.001")),
            ),
        )

    def test_research_registry_gates_acceptance_and_never_creates_approval(self):
        now = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
        experiment = self.build_experiment(now)
        with tempfile.TemporaryDirectory() as directory:
            database = CioDatabase(Path(directory) / "cio.db")
            experiment_id = database.record_research_experiment(experiment)
            first = self.build_run(experiment_id, now, 6, "d")
            with self.assertRaisesRegex(PolicyViolation, "paper status"):
                database.record_research_run(first)
            self.assertEqual(database.transition_research_experiment(experiment_id, "paper")["status"], "paper")
            database.record_research_run(first)
            with self.assertRaisesRegex(PolicyViolation, "requires 10"):
                database.transition_research_experiment(experiment_id, "accepted", approved_by="operator")
            database.record_research_run(self.build_run(experiment_id, now + timedelta(days=1), 4, "f"))
            with self.assertRaisesRegex(PolicyViolation, "human approver"):
                database.transition_research_experiment(experiment_id, "accepted")
            accepted = database.transition_research_experiment(
                experiment_id, "accepted", approved_by="operator",
            )
            self.assertEqual(accepted["status"], "accepted")
            self.assertEqual(accepted["observations"], 10)
            self.assertEqual(database.list_approvals(), [])

    def test_research_experiment_requires_replay_and_rollback_plan(self):
        experiment = self.build_experiment(datetime.now(timezone.utc))
        invalid = ResearchExperiment(**{**experiment.__dict__, "replay_snapshot_sha256": "missing"})
        with self.assertRaisesRegex(PolicyViolation, "replay_snapshot_sha256"):
            _ = invalid.experiment_id


if __name__ == "__main__":
    unittest.main()
