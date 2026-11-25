"""Phase 8.9: deployment, expiration, PIT datasets, walk-forward and safety."""
import json
import subprocess
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from robinhood_tools import pit_dataset
from robinhood_tools.workflow_manifest import read_manifest, write_manifest
from scripts import champion_equiv_benchmark as champion
from scripts import checkout_parity, deploy_hardened_launchers as deploy
from scripts import operational_reliability, signal_expiration_report
from scripts import strategy_replay_guard, walk_forward_evaluator

WT = Path(__file__).resolve().parents[1]
SCRIPTS = WT / "scripts"
DATE = "2026-11-24"
KEY = "a" * 64
BARS = [{"t": f"2026-11-24T{9 + i // 12:02d}:{(i % 12) * 5:02d}:00-05:00",
         "open": 100 + i, "high": 101 + i, "low": 99 + i,
         "close": 100 + i, "volume": 1000 + i} for i in range(24)]


class ParityAndDeploymentTests(unittest.TestCase):
    def test_allowlist_exact_and_no_owner_files(self):
        self.assertEqual(len(checkout_parity.ALLOWLIST), 26)
        for rel in checkout_parity.ALLOWLIST:
            self.assertNotIn(".env", rel)
            self.assertNotIn("superpowers", rel)

    def test_hash_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src, dst = Path(a), Path(b)
            rel = "scripts/intraday_loop.bat"
            (src / rel).parent.mkdir(parents=True)
            (dst / rel).parent.mkdir(parents=True)
            (src / rel).write_text("new")
            (dst / rel).write_text("old")
            report = checkout_parity.compare(src, dst, allowlist=(rel,))
            self.assertEqual(report["rows"][0]["classification"],
                             checkout_parity.DEPLOY)

    def test_conflict_blocks_copy(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src, dst = Path(a), Path(b)
            rel = "scripts/intraday_loop.bat"
            (src / rel).parent.mkdir(parents=True)
            (dst / rel).parent.mkdir(parents=True)
            (src / rel).write_text("same")
            (dst / rel).write_text("same")
            with mock.patch.object(checkout_parity, "_locally_modified", return_value=True), \
                    mock.patch.object(deploy, "running_ai_tasks", return_value=[]):
                result = deploy.apply(src, dst, backup_root=Path(tempfile.gettempdir()),
                                      owner_authorized=True, require_all_sources=False,
                                      now=datetime(2026, 11, 25, 2, 0))
            self.assertFalse(result["applied"])

    def test_running_task_blocks_apply_and_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(deploy, "running_ai_tasks", return_value=["AI-Desk-X"]):
                result = deploy.apply(Path(td), Path(td), backup_root=Path(td),
                                      owner_authorized=True, require_all_sources=False,
                                      now=datetime(2026, 11, 25, 2, 0))
                self.assertFalse(result["applied"])
                manifest = Path(td) / "m.json"
                manifest.write_text(json.dumps({"destination": str(td),
                                                "backup_dir": str(td), "files": []}),
                                    encoding="utf-8")
                rollback = deploy.rollback(manifest)
            self.assertIn("running", rollback.get("error", ""))

    def test_partial_deploy_rollback_restores_files(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                tempfile.TemporaryDirectory() as backups:
            src, dst = Path(a), Path(b)
            rel = "scripts/intraday_loop.bat"
            (src / rel).parent.mkdir(parents=True)
            (src / rel).write_text("new")
            (dst / rel).parent.mkdir(parents=True)
            (dst / rel).write_text("old")
            with mock.patch.object(deploy, "running_ai_tasks", return_value=[]), \
                    mock.patch.object(deploy.os, "replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    deploy.apply(src, dst, backup_root=Path(backups),
                                 owner_authorized=True, require_all_sources=False,
                                 now=datetime(2026, 11, 25, 2, 0))
            manifest = next(Path(backups).glob("*/backup-manifest.json"))
            with mock.patch.object(deploy, "running_ai_tasks", return_value=[]):
                rollback = deploy.rollback(manifest)
            self.assertEqual((dst / rel).read_text(encoding="utf-8"), "old")
            self.assertTrue(rollback["restored"])

    def test_active_window_guard(self):
        self.assertTrue(deploy.active_window(datetime(2026, 11, 25, 10, 30)))
        self.assertFalse(deploy.active_window(datetime(2026, 11, 25, 2, 0)))

    def test_source_commit_binding_uses_short_head(self):
        expected = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=WT,
                                  capture_output=True, text=True, timeout=60).stdout.strip()
        self.assertEqual(deploy.source_commit(WT), expected)
        self.assertEqual(len(expected), 7)

    def test_registered_task_paths_from_inventory(self):
        source = (SCRIPTS / "operational_reliability.py").read_text(encoding="utf-8")
        for task in ("AI-Desk-Flatten-1545", "AI-Desk-OvernightEnter",
                     "AI-CIO-AutoTrade-0945", "AI-Desk-WeeklyReview-1645"):
            self.assertIn(task, source)


class ExpirationTests(unittest.TestCase):
    @staticmethod
    def _stamps():
        return {
            "setup_formation_time": "2026-11-24T14:30:00-05:00",
            "last_completed_bar_time": "2026-11-24T14:35:00-05:00",
            "signal_detection_time": "2026-11-24T14:40:00-05:00",
            "research_queue_entry": "2026-11-24T14:41:00-05:00",
            "deterministic_gate_time": "2026-11-24T14:42:00-05:00",
            "authorization_time": "2026-11-24T14:43:00-05:00",
            "signal_expiration": "2026-11-24T14:50:00-05:00",
            "source_quote_age_at_decision_s": 30,
            "decision_et": "2026-11-24T14:44:00-05:00",
        }

    def test_all_five_classes_reachable(self):
        base = self._stamps()
        self.assertEqual(signal_expiration_report.classify(
            {"technical_setup": "long_reclaim", "before_entry_cutoff": True,
             **base})["classification"], signal_expiration_report.ACTIONABLE)
        self.assertEqual(signal_expiration_report.classify(
            {"technical_setup": "long_reclaim", "before_entry_cutoff": True,
             **{**base, "source_quote_age_at_decision_s": 400}})["classification"],
            signal_expiration_report.EXPIRED)
        self.assertEqual(signal_expiration_report.classify(
            {"before_entry_cutoff": False})["classification"],
            signal_expiration_report.CUTOFF)
        self.assertEqual(signal_expiration_report.classify(
            {"before_entry_cutoff": True})["classification"],
            signal_expiration_report.INSUFFICIENT)
        self.assertEqual(signal_expiration_report.classify(
            {"technical_setup": "unsupported", "before_entry_cutoff": True})["classification"],
            signal_expiration_report.INELIGIBLE)

    def test_missing_cutoff_is_insufficient(self):
        self.assertEqual(signal_expiration_report.classify(
            {**self._stamps()})["classification"],
            signal_expiration_report.INSUFFICIENT)

    def test_out_of_order_timestamps_are_insufficient(self):
        row = {**self._stamps(), "before_entry_cutoff": True,
               "signal_detection_time": "2026-11-24T14:45:00-05:00"}
        self.assertEqual(signal_expiration_report.classify(row)["classification"],
                         signal_expiration_report.INSUFFICIENT)

    def test_negative_quote_age_is_expired(self):
        row = {**self._stamps(), "before_entry_cutoff": True,
               "source_quote_age_at_decision_s": -5}
        self.assertEqual(signal_expiration_report.classify(row)["classification"],
                         signal_expiration_report.EXPIRED)

    def test_real_latency_artifact_classification(self):
        path = WT / "docs/certification/phase-8-9-signal-expiration.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(report["counts"]["ACTIONABLE_AT_DECISION"], 0)
        self.assertEqual(report["counts"]["SESSION_CUTOFF_EXCEEDED"], 1)
        self.assertEqual(report["counts"]["INSUFFICIENT_EVIDENCE"], 31)

    def test_no_new_entry_policy_unchanged(self):
        source = (SCRIPTS / "daytrade_engine.py").read_text(encoding="utf-8")
        self.assertIn('NO_NEW_ENTRIES_AFTER = "15:15"', source)
        self.assertIn('FLATTEN_AT = "15:45"', source)


class DatasetAndWalkForwardTests(unittest.TestCase):
    def test_write_and_audit_synthetic_fixture(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"CIO_SNAPSHOT_HMAC_KEY": KEY}):
            root = Path(td)
            meta = pit_dataset.write_snapshot(
                root, dataset_id="fixture", session_date=DATE, symbol="QQQ",
                decision_time_utc="2026-11-24T15:00:00-05:00",
                data_type="synthetic_fixture", bars=BARS, provider="fixture",
                feed="fixture", volume_basis="synthetic", timeframe="5m",
                strategy_version="test", split="dev",
                retrieved_at_utc="2026-11-24T15:30:00-05:00")
            self.assertEqual(meta["data_type"], "synthetic_fixture")
            audit = pit_dataset.audit(root, "fixture")
            self.assertFalse(audit["accepted_for_replay"])

    def test_invalid_data_type_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(pit_dataset.DatasetError):
                pit_dataset.write_snapshot(
                    Path(td), dataset_id="x", session_date=DATE, symbol="QQQ",
                    decision_time_utc="2026-11-24T15:00:00-05:00",
                    data_type="actual_broker_fill", bars=BARS, provider="p",
                    feed="f", volume_basis="v", timeframe="5m",
                    strategy_version="s", split="dev")

    def test_future_bar_rejected(self):
        bars = BARS + [{"t": "2026-11-24T16:00:00-05:00", "open": 1, "high": 2,
                        "low": 1, "close": 2, "volume": 10}]
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(pit_dataset.DatasetError):
                pit_dataset.write_snapshot(
                    Path(td), dataset_id="x", session_date=DATE, symbol="QQQ",
                    decision_time_utc="2026-11-24T15:00:00-05:00",
                    data_type="historical_backfill", bars=bars, provider="p",
                    feed="f", volume_basis="v", timeframe="5m",
                    strategy_version="s", split="dev")

    def test_duplicate_decision_key_detected(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"CIO_SNAPSHOT_HMAC_KEY": KEY}):
            root = Path(td)
            kw = dict(dataset_id="dup", session_date=DATE, symbol="QQQ",
                      decision_time_utc="2026-11-24T15:00:00-05:00",
                      data_type="historical_backfill", bars=BARS, provider="p",
                      feed="f", volume_basis="v", timeframe="5m",
                      strategy_version="s", split="dev")
            pit_dataset.write_snapshot(root, cycle_id="c1", **kw)
            with self.assertRaises(pit_dataset.DatasetError):
                pit_dataset.write_snapshot(root, cycle_id="c2", **kw)
            audit = pit_dataset.audit(root, "dup")
            self.assertEqual(audit["entries"], 1)

    def test_walk_forward_split_and_holdout(self):
        dates = [f"2026-11-{d:02d}" for d in range(1, 21)]
        splits = walk_forward_evaluator.assign_splits(dates)
        self.assertTrue(set(splits["dev"]).isdisjoint(splits["final"]))
        self.assertTrue(set(splits["validation"]).isdisjoint(splits["final"]))

    def test_walk_forward_rejects_unverified_dataset(self):
        with tempfile.TemporaryDirectory() as td:
            report = walk_forward_evaluator.evaluate(Path(td), "missing")
            self.assertEqual(report["verdict"], "NOT_VERIFIED")
            self.assertEqual(report["promotion"], "none")

    def test_cost_scenarios_and_borrow_label(self):
        with tempfile.TemporaryDirectory() as td:
            report = walk_forward_evaluator.evaluate(Path(td), "missing")
            self.assertIn("borrow_bps_day", report["cost_scenarios"])
            self.assertEqual(report["results"]["long_reclaim"]["borrow_evidence"],
                             "NOT VERIFIED - no broker snapshot")

    def test_replay_guard_rejects_post_close(self):
        ok, reason = strategy_replay_guard.usable(
            {"freshness": "post_close", "bars": [{"t": "2026-11-24T15:55:00-05:00"}],
             "data_completeness": "complete"}, as_of="2026-11-24T15:00:00-05:00")
        self.assertFalse(ok)


class ObservabilityAndSafetyTests(unittest.TestCase):
    def test_expand_observability_fields(self):
        source = (SCRIPTS / "champion_equiv_benchmark.py").read_text(encoding="utf-8")
        for token in ("llm_completions", "stage_duration_ms", "queue_delay_ms",
                      "freshness_at_decision", "cache_hit_note", "on_llm_retry"):
            self.assertIn(token, source)

    def test_tool_counts_reconcile(self):
        events = [{"event": "tool_start", "tool_invocation_id": "a"},
                  {"event": "tool_end", "tool_invocation_id": "a"}]
        summary = champion.summarize_tool_events(events)
        self.assertEqual(summary["attempted"], 1)
        self.assertEqual(summary["completed"], 1)

    def test_dashboard_session_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_manifest(root, task="flatten-verify", date=DATE, status="VERIFIED",
                           run_id="r", started_at="s", finished_at="f", exit_code=0,
                           mode="test")
            payload = operational_reliability.operational_reliability_payload(root, date=DATE)
        self.assertIn("session_readiness", payload)
        self.assertIn("signal_expiration", payload["session_readiness"])
        self.assertEqual(payload["session_readiness"]["paper_pilot"],
                         "NOT AUTHORIZED - readiness package only")

    def test_dashboard_auth_mode_derived(self):
        source = (SCRIPTS / "operational_reliability.py").read_text(encoding="utf-8")
        self.assertIn("_enforcement_on()", source)

    def test_flatten_no_reversal(self):
        from scripts import flatten_daytrades
        qty, side, reason = flatten_daytrades.close_plan(
            {"ticker": "QQQ", "quantity": 10}, __import__("decimal").Decimal("-5"), set())
        self.assertEqual(side, "buy")
        self.assertEqual(qty, 5)

    def test_authorization_replay_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            from robinhood_tools.workflow_manifest import FAILED
            write_manifest(Path(td), task="auth", date=DATE, status=FAILED,
                           run_id="r", started_at="s", finished_at="f", exit_code=1,
                           mode="test")
            self.assertIsNone(read_manifest(Path(td), "auth", "2026-11-23"))

    def test_shadow_no_order_paths(self):
        for name in ("walk_forward_evaluator.py", "signal_expiration_report.py",
                     "pit_dataset.py"):
            source = (SCRIPTS / name).read_text(encoding="utf-8") if (SCRIPTS / name).exists() \
                else (WT / "robinhood_tools" / name).read_text(encoding="utf-8")
            self.assertNotIn("place_signal_order(", source)

    def test_no_unauthorized_order_submission(self):
        source = (WT / "docs/specs/intraday-setups/v1.0.0/EXPIRATION.md").read_text(
            encoding="utf-8")
        self.assertIn("no new trading", source.lower())


if __name__ == "__main__":
    unittest.main()
