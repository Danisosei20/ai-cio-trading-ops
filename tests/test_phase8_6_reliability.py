"""Phase 8.6: wrapper exit codes, manifests, flatten and graph harness."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from robinhood_tools.workflow_manifest import (FAILED, NO_ACTION_REQUIRED,
                                               RECONCILIATION_REQUIRED, VERIFIED,
                                               read_manifest, write_manifest)
from scripts import flatten_verified, workflow_runner

DATE = "2026-11-24"


class ManifestTests(unittest.TestCase):
    def test_terminal_schema_and_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = write_manifest(root, task="flatten", date=DATE,
                                    status=NO_ACTION_REQUIRED, run_id="r1",
                                    started_at="s", finished_at="f", exit_code=0,
                                    mode="paper_read_only")
            doc = read_manifest(root, "flatten", DATE)
            self.assertEqual(doc["status"], NO_ACTION_REQUIRED)
            self.assertTrue(doc["terminal"])
            self.assertEqual(doc["sha256"], result["sha256"])

    def test_tampered_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_manifest(root, task="flatten", date=DATE, status=VERIFIED,
                           run_id="r1", started_at="s", finished_at="f",
                           exit_code=0, mode="test")
            path = root / "outputs/runs" / DATE / "flatten.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["exit_code"] = 9
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(read_manifest(root, "flatten", DATE))

    def test_plain_sha256_forgery_is_rejected(self):
        """A forger who only knows the old unkeyed scheme must still fail."""
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_manifest(root, task="flatten", date=DATE, status=VERIFIED,
                           run_id="r1", started_at="s", finished_at="f",
                           exit_code=0, mode="test")
            path = root / "outputs/runs" / DATE / "flatten.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["status"] = NO_ACTION_REQUIRED
            unsigned = {k: v for k, v in doc.items() if k != "sha256"}
            doc["sha256"] = hashlib.sha256(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode()).hexdigest()
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(read_manifest(root, "flatten", DATE))

    def test_hash_mode_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_manifest(root, task="flatten", date=DATE, status=VERIFIED,
                           run_id="r1", started_at="s", finished_at="f",
                           exit_code=0, mode="test")
            path = root / "outputs/runs" / DATE / "flatten.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["hash_mode"] = "attacker-value"
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(read_manifest(root, "flatten", DATE))

    def test_invalid_task_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                write_manifest(Path(td), task="../evil", date=DATE,
                               status=VERIFIED, run_id="r1", started_at="s",
                               finished_at="f", exit_code=0, mode="test")


class RunnerTests(unittest.TestCase):
    def test_success_and_failure_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            python = sys.executable
            ok = workflow_runner.run_stage(
                root, task="ok-task", date=DATE, mode="test",
                command=[python, "-c", "print('ok')"], timeout=10,
                expect_artifact=None, require_artifact=False)
            self.assertEqual(ok["status"], VERIFIED)
            self.assertEqual(ok["exit_code"], 0)
            bad = workflow_runner.run_stage(
                root, task="bad-task", date=DATE, mode="test",
                command=[python, "-c", "raise SystemExit(7)"], timeout=10,
                expect_artifact=None, require_artifact=False)
            self.assertEqual(bad["status"], FAILED)
            self.assertEqual(bad["exit_code"], 7)

    def test_missing_artifact_is_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = workflow_runner.run_stage(
                root, task="no-artifact", date=DATE, mode="test",
                command=[sys.executable, "-c", "print('done')"], timeout=10,
                expect_artifact="outputs/missing.json")
            self.assertEqual(result["status"], FAILED)

    def test_timeout_is_structured_failure(self):
        with tempfile.TemporaryDirectory() as td:
            result = workflow_runner.run_stage(
                Path(td), task="slow", date=DATE, mode="test",
                command=[sys.executable, "-c", "import time; time.sleep(2)"],
                timeout=0.2, expect_artifact=None, require_artifact=False)
            self.assertEqual(result["status"], FAILED)
            self.assertEqual(result["exit_code"], 124)

    def test_stale_artifact_is_failure(self):
        import os
        import time
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact = root / "outputs/stale.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("{}", encoding="utf-8")
            old = time.time() - 3600
            os.utime(artifact, (old, old))
            result = workflow_runner.run_stage(
                root, task="stale", date=DATE, mode="test",
                command=[sys.executable, "-c", "print('done')"], timeout=10,
                expect_artifact="outputs/stale.json")
            self.assertEqual(result["status"], FAILED)

    def test_stderr_is_redacted(self):
        synthetic = "api" + "_key=SUPERSECRET123"
        redacted = workflow_runner.redact(synthetic)
        self.assertNotIn("SUPERSECRET123", redacted)
        self.assertIn("redacted", redacted)
        self.assertNotIn("AKIAEXAMPLEKEY123",
                         workflow_runner.redact("AKIAEXAMPLEKEY123"))


class FlattenTests(unittest.TestCase):
    def test_no_positions_is_verified_no_action(self):
        class Backend:
            def list_positions(self):
                return []
            def list_open_orders(self):
                return []
            def market_clock(self):
                return {"is_open": False}
        class Transport:
            def request(self, method, path):
                return {"equity": "100"}
        backend = Backend()
        backend.transport = Transport()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(flatten_verified, "broker_state",
                                   return_value={"positions": [], "open_orders": [],
                                                 "account_equity": "100",
                                                 "clock_is_open": False,
                                                 "source": "fake"}):
                result = flatten_verified.verify(root, DATE)
            self.assertEqual(result["status"], VERIFIED)
            self.assertEqual(result["exit_code"], 0)
            doc = read_manifest(root, "flatten-verify", DATE)
            self.assertEqual(doc["status"], VERIFIED)
            self.assertEqual(doc["evidence"]["flat_scope"], "whole_account")

    def test_owned_position_is_reconciliation_required(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auto = root / "outputs/paper/tradingagents/QQQ" / DATE / "auto.json"
            auto.parent.mkdir(parents=True, exist_ok=True)
            auto.write_text(json.dumps({"ticker": "QQQ", "action": "buy_placed",
                                        "trade_style": "day_trade"}), encoding="utf-8")
            with mock.patch.object(flatten_verified, "broker_state",
                                   return_value={"positions": [{"symbol": "QQQ", "qty": 5}],
                                                 "open_orders": [],
                                                 "account_equity": "100",
                                                 "clock_is_open": False,
                                                 "source": "fake"}):
                result = flatten_verified.verify(root, DATE)
            self.assertEqual(result["status"], RECONCILIATION_REQUIRED)
            self.assertEqual(result["exit_code"], 1)

    def test_broker_holding_without_owned_record_is_reconciliation_required(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(flatten_verified, "broker_state",
                                   return_value={"positions": [
                                       {"symbol": "NVDA", "qty": 100}],
                                       "open_orders": [],
                                       "account_equity": "100",
                                       "clock_is_open": False,
                                       "queried_at": __import__("time").time(),
                                       "source": "fake"}):
                result = flatten_verified.verify(root, DATE)
            self.assertEqual(result["status"], RECONCILIATION_REQUIRED)
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["evidence"]["held_equity_symbols"], ["NVDA"])

    def test_overnight_position_is_not_flattened_or_misclassified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "outputs/paper/overnight.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"date": DATE,
                                        "entries": [{"ticker": "QQQ"}]}),
                            encoding="utf-8")
            with mock.patch.object(flatten_verified, "broker_state",
                                   return_value={"positions": [
                                       {"symbol": "QQQ", "qty": 10}],
                                       "open_orders": [],
                                       "account_equity": "100",
                                       "clock_is_open": False,
                                       "source": "fake"}):
                result = flatten_verified.verify(root, DATE)
            self.assertEqual(result["status"], NO_ACTION_REQUIRED)
            self.assertIn("QQQ", result["evidence"]["overnight_symbols_excluded"])

    def test_broker_failure_never_reports_success(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(flatten_verified, "broker_state",
                                   side_effect=RuntimeError("offline")):
                result = flatten_verified.verify(root, DATE)
            self.assertEqual(result["status"], RECONCILIATION_REQUIRED)

    def test_no_order_submission_path(self):
        source = Path(flatten_verified.__file__).read_text(encoding="utf-8")
        for token in ("place_signal_order(", "place_long_option(", "submit_order("):
            self.assertNotIn(token, source)
