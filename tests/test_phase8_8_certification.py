"""Phase 8.8: checkout parity, deployment safety, telemetry, latency and validation."""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from robinhood_tools import latency_ledger
from robinhood_tools import workflow_manifest
from scripts import champion_equiv_benchmark as champion
from scripts import checkout_parity, deploy_hardened_launchers as deploy
from scripts import flatten_daytrades, operational_reliability, signal_latency_report
from scripts import strategy_replay_guard

WT = Path(__file__).resolve().parents[1]
SCRIPTS = WT / "scripts"
DATE = "2026-11-24"


class ParityTests(unittest.TestCase):
    def test_allowlist_excludes_secrets_and_owner_files(self):
        for rel in checkout_parity.ALLOWLIST:
            self.assertNotIn(".env", rel)
            self.assertNotIn("superpowers", rel)
            self.assertNotIn("README", rel)

    def test_match_and_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src, dst = Path(a), Path(b)
            rel = "scripts/intraday_loop.bat"
            (src / rel).parent.mkdir(parents=True)
            (src / rel).write_text("@echo off\n")
            (dst / rel).parent.mkdir(parents=True)
            (dst / rel).write_text("@echo off\n")
            report = checkout_parity.compare(src, dst, allowlist=(rel,))
            self.assertEqual(report["counts"]["MATCH"], 1)
            (dst / rel).write_text("@echo off\nREM changed\n")
            report = checkout_parity.compare(src, dst, allowlist=(rel,))
            self.assertEqual(report["rows"][0]["classification"],
                             checkout_parity.DEPLOY)

    def test_conflict_detection(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src, dst = Path(a), Path(b)
            rel = "scripts/intraday_loop.bat"
            (src / rel).parent.mkdir(parents=True)
            (src / rel).write_text("same")
            (dst / rel).parent.mkdir(parents=True)
            (dst / rel).write_text("same")
            with mock.patch.object(checkout_parity, "_locally_modified", return_value=True):
                report = checkout_parity.compare(src, dst, allowlist=(rel,))
            self.assertEqual(report["rows"][0]["classification"],
                             checkout_parity.CONFLICT)

    def test_deployment_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src, dst = Path(a), Path(b)
            rel = "scripts/intraday_loop.bat"
            (src / rel).parent.mkdir(parents=True)
            (src / rel).write_text("@echo off\n")
            with mock.patch.object(deploy, "running_ai_tasks", return_value=[]):
                result = deploy.plan(src, dst, now=datetime(2026, 11, 25, 2, 0))
            self.assertFalse(result["applied"])
            self.assertFalse((dst / rel).exists())
            self.assertFalse(result["secrets_touched"])
            self.assertFalse(result["scheduler_modified"])

    def test_apply_and_rollback_roundtrip(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                tempfile.TemporaryDirectory() as backups:
            src, dst = Path(a), Path(b)
            rel = "scripts/intraday_loop.bat"
            (src / rel).parent.mkdir(parents=True)
            (src / rel).write_text("@echo off\nnew\n")
            (dst / rel).parent.mkdir(parents=True)
            (dst / rel).write_text("@echo off\nold\n")
            before = checkout_parity.sha256(dst / rel)
            with mock.patch.object(deploy, "running_ai_tasks", return_value=[]):
                result = deploy.apply(src, dst, backup_root=Path(backups),
                                      owner_authorized=True, require_all_sources=False,
                                      now=datetime(2026, 11, 25, 2, 0))
            self.assertTrue(result["applied"])
            self.assertEqual((dst / rel).read_text(encoding="utf-8"),
                             "@echo off\nnew\n")
            rollback = deploy.rollback(Path(result["backup_manifest"]))
            self.assertEqual(rollback["restored"], [rel])
            self.assertEqual(checkout_parity.sha256(dst / rel), before)

    def test_active_window_blocks_batch_replacement(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src, dst = Path(a), Path(b)
            rel = "scripts/intraday_loop.bat"
            (src / rel).parent.mkdir(parents=True)
            (src / rel).write_text("new")
            (dst / rel).parent.mkdir(parents=True)
            (dst / rel).write_text("old")
            with mock.patch.object(deploy, "running_ai_tasks", return_value=[]):
                result = deploy.apply(src, dst, backup_root=Path(tempfile.gettempdir()),
                                      owner_authorized=True, require_all_sources=False,
                                      now=datetime(2026, 11, 25, 10, 30))
            self.assertFalse(result["applied"])
            self.assertIn("09:45-16:05", result.get("error", ""))
            self.assertEqual((dst / rel).read_text(encoding="utf-8"), "old")

    def test_cli_apply_forwards_owner_authorization(self):
        source = (SCRIPTS / "deploy_hardened_launchers.py").read_text(encoding="utf-8")
        self.assertIn("owner_authorized=args.owner_authorized", source)

    def test_apply_requires_owner_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            p = subprocess.run([sys.executable, str(SCRIPTS / "deploy_hardened_launchers.py"),
                                "--destination", td, "--apply"], cwd=WT,
                               capture_output=True, text=True, timeout=60)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("--owner-authorized", p.stdout + p.stderr)


class SchedulerAndRunnerTests(unittest.TestCase):
    def test_registered_task_inventory_is_read_only(self):
        source = (SCRIPTS / "operational_reliability.py").read_text(encoding="utf-8")
        self.assertIn("AI-Desk-Flatten-1545", source)
        self.assertIn("Operational log disabled", source)

    def test_wrong_interpreter_detected(self):
        with tempfile.TemporaryDirectory() as td:
            from scripts import workflow_runner
            result = workflow_runner.run_stage(
                Path(td), task="venv", date=DATE, mode="test",
                command=[sys.executable, "-c", "pass"], timeout=5,
                expect_artifact=None, require_artifact=False,
                expected_python=str(Path(td) / "wrong/python.exe"))
            self.assertEqual(result["exit_code"], 127)

    def test_child_rc_and_stale_artifact(self):
        from scripts import workflow_runner
        import os
        import time
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad = workflow_runner.run_stage(
                root, task="rc", date=DATE, mode="test",
                command=[sys.executable, "-c", "raise SystemExit(7)"], timeout=5,
                expect_artifact=None, require_artifact=False)
            self.assertEqual(bad["exit_code"], 7)
            artifact = root / "outputs/stale.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("{}", encoding="utf-8")
            old = time.time() - 3600
            os.utime(artifact, (old, old))
            stale = workflow_runner.run_stage(
                root, task="stale", date=DATE, mode="test",
                command=[sys.executable, "-c", "pass"], timeout=5,
                expect_artifact="outputs/stale.json")
            self.assertEqual(stale["status"], "FAILED")


class TelemetryTests(unittest.TestCase):
    def test_tool_event_summary_counts_real_dispatches(self):
        events = [
            {"event": "tool_start", "tool_invocation_id": "a", "tool_category": "market_data"},
            {"event": "tool_end", "tool_invocation_id": "a"},
            {"event": "tool_start", "tool_invocation_id": "b", "tool_category": "news"},
            {"event": "tool_error", "tool_invocation_id": "b"},
        ]
        summary = champion.summarize_tool_events(events)
        self.assertEqual(summary["attempted"], 2)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["unique_invocations"], 2)
        self.assertFalse(summary["secrets_recorded"])

    def test_stale_tool_events_cleared_per_run(self):
        source = (SCRIPTS / "champion_equiv_benchmark.py").read_text(encoding="utf-8")
        self.assertIn("stale_events.unlink()", source)
        self.assertIn("events_file.unlink()", source)

    def test_zero_tool_events_never_certified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / "outputs/shadow/research/no-tools"
            run.mkdir(parents=True)
            class FakeProc:
                pid = 1
                returncode = 0
                def communicate(self, timeout=None):
                    (run / "child_result.json").write_text(json.dumps({
                        "decision": "UNKNOWN", "error": "", "config_match": True,
                        "reports": {"market_report": True, "news_report": True},
                        "counts": {"tool_calls": 0}, "config_digest": "x"}),
                        encoding="utf-8")
                    return "", ""
            with mock.patch.object(champion.subprocess, "Popen", return_value=FakeProc()):
                result = champion.measure(ta_python=Path("python"), ta_root=Path("ta"),
                                          run_id="no-tools", root=root, symbol="NVDA",
                                          date=DATE, deadline=5)
            self.assertEqual(result["status"], "error")
            self.assertIn("telemetry NOT VERIFIED", result["error"])

    def test_telemetry_schema_has_no_secret_fields(self):
        source = (SCRIPTS / "champion_equiv_benchmark.py").read_text(encoding="utf-8")
        for field in ("tool_invocation_id", "model_request_id", "start_monotonic_ns",
                      "safe_args", "cache_hit", "evidence_ref"):
            self.assertIn(field, source)
        self.assertIn("ALLOWED_TOOL_KEYS", source)

    def test_full_graph_deadline_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(champion.subprocess, "Popen",
                                   side_effect=subprocess.TimeoutExpired("x", 1)):
                report = champion.measure(ta_python=Path("python"), ta_root=Path("ta"),
                                          run_id="deadline", root=Path(td), symbol="NVDA",
                                          date=DATE, deadline=1)
            self.assertEqual(report["status"], "timeout")
            self.assertTrue((Path(td) / "outputs/shadow/research/deadline/"
                             "measurement.json").exists())


class LatencyTests(unittest.TestCase):
    def test_ledger_record_and_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = 1_000_000_000
            for stage, offset in (("last_bar", 0), ("signal_detected", 100),
                                  ("research_start", 200), ("research_end", 1200),
                                  ("decision", 1400)):
                latency_ledger.record(root, DATE, "run1", symbol="NVDA", stage=stage,
                                      monotonic_ns=base + offset)
            metrics = latency_ledger.analyze(latency_ledger.load(root, DATE, "run1"))
            self.assertEqual(metrics["research_duration_ms"], 0.001)
            self.assertEqual(metrics["total_signal_to_decision_ms"], 0.0013)
            self.assertEqual(metrics["age_at_decision_ms"], 0.0014)

    def test_missing_stage_is_null_not_estimated(self):
        metrics = latency_ledger.analyze([
            {"stage": "research_start", "monotonic_ns": 10},
            {"stage": "decision", "monotonic_ns": 20}])
        self.assertIsNone(metrics["total_signal_to_decision_ms"])

    def test_log_report_does_not_fabricate_missing_fields(self):
        with tempfile.TemporaryDirectory() as td:
            live = Path(td)
            log = live / "outputs/paper/tradingagents/live.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                f"{DATE}T10:00:00-05:00 [NVDA] researching ({DATE}) via nvidia/x\n"
                f"{DATE}T10:05:00-05:00 [NVDA] decision: Sell\n", encoding="utf-8")
            result = signal_latency_report.report(live, DATE)
            self.assertEqual(result["observations"][0]["research_duration_s"], 300.0)
            self.assertIsNone(result["observations"][0]["source_quote_age_at_decision_s"])
            self.assertTrue(result["observations"][0]["before_entry_cutoff"])

    def test_late_research_is_outside_cutoff(self):
        with tempfile.TemporaryDirectory() as td:
            live = Path(td)
            log = live / "outputs/paper/tradingagents/live.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                f"{DATE}T15:20:00-05:00 [NVDA] researching ({DATE}) via nvidia/x\n"
                f"{DATE}T15:28:00-05:00 [NVDA] decision: Sell\n", encoding="utf-8")
            result = signal_latency_report.report(live, DATE)
            self.assertFalse(result["observations"][0]["before_entry_cutoff"])


class StrategyValidationTests(unittest.TestCase):
    def test_replay_guard_rejects_post_close_and_future_bars(self):
        ok, reason = strategy_replay_guard.usable(
            {"freshness": "post_close", "bars": [{"t": "2026-11-24T15:55:00-05:00"}],
             "data_completeness": "complete"}, as_of="2026-11-24T14:45:00-05:00")
        self.assertFalse(ok)
        self.assertIn("post_close", reason)
        ok, reason = strategy_replay_guard.usable(
            {"freshness": "fresh", "data_completeness": "complete",
             "bars": [{"t": "2026-11-24T15:00:00-05:00"}]},
            as_of="2026-11-24T14:45:00-05:00")
        self.assertFalse(ok)
        self.assertIn("future", reason)

    def test_duplicate_bar_sets_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            snap = {"bars": [{"t": "t1", "close": 1}]}
            for cycle in ("cycle-1400", "cycle-1445"):
                path = root / "outputs/market_snapshots" / DATE / cycle / "snapshot.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(snap), encoding="utf-8")
            result = strategy_replay_guard.audit_set(root, DATE)
            self.assertEqual(result["unique_bar_sets"], 1)
            self.assertNotEqual(result["verdict"], "verified")

    def test_or_boundaries_and_direction(self):
        from robinhood_tools import bar_validation
        from scripts import scout_factory_compare as compare
        ok, reason = bar_validation.long_reclaim(
            101, vwap=100, or_high=102, or_low=99)
        self.assertFalse(ok)
        self.assertEqual(reason, "price not above opening-range high")
        ok, _ = bar_validation.short_breakdown(
            99, vwap=100, or_high=102, or_low=95)
        self.assertFalse(ok)
        self.assertEqual(compare.scout_classify(
            price=101, vwap=100, or_high=102, or_low=99, bars=12)[1],
            "long_reclaim")

    def test_oos_is_not_claimed(self):
        html = (WT / "web/agent-office/trading.html").read_text(encoding="utf-8")
        self.assertIn("deployment-readiness", html)
        source = (SCRIPTS / "operational_reliability.py").read_text(encoding="utf-8")
        self.assertIn("NOT VERIFIED - insufficient point-in-time data", source)


class DeploymentSafetyTests(unittest.TestCase):
    def test_partial_deploy_writes_rollback_manifest(self):
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
            manifests = list(Path(backups).glob("*/backup-manifest.json"))
            self.assertTrue(manifests)
            self.assertTrue(json.loads(manifests[0].read_text(
                encoding="utf-8"))["partial"] is True)

    def test_rollback_removes_created_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            backup = root / "backup"
            backup.mkdir()
            rel = "scripts/new_module.py"
            manifest = {"destination": str(root / "dest"), "backup_dir": str(backup),
                        "files": [{"component": rel, "created": True,
                                   "backup_sha256": None}]}
            dest = root / "dest" / rel
            dest.parent.mkdir(parents=True)
            dest.write_text("deployed")
            path = backup / "backup-manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch.object(deploy, "running_ai_tasks", return_value=[]):
                result = deploy.rollback(path)
            self.assertEqual(result["removed"], [rel])
            self.assertFalse(dest.exists())


class SafetyTests(unittest.TestCase):
    def test_working_orders_use_symbol_and_side(self):
        rec = {"ticker": "QQQ", "quantity": 3}
        qty, side, reason = flatten_daytrades.close_plan(
            rec, Decimal("5"), {("QQQ", "sell")})
        self.assertEqual(reason, "a close order is already working")
        qty, side, reason = flatten_daytrades.close_plan(
            rec, Decimal("-5"), {("QQQ", "buy")})
        self.assertEqual(reason, "a close order is already working")

    def test_duplicate_close_refresh_marker_exists(self):
        source = (SCRIPTS / "flatten_daytrades.py").read_text(encoding="utf-8")
        self.assertIn("positions[symbol] = held - qty", source)
        self.assertIn("working.add((symbol, side))", source)

    def test_overnight_exit_dedupes_and_checks_working_orders(self):
        source = (SCRIPTS / "overnight_pilot.py").read_text(encoding="utf-8")
        self.assertIn("seen_exit_symbols", source)
        self.assertIn("working_orders", source)

    def test_position_effect_binding(self):
        from robinhood_tools import execution_authorization as ea
        with mock.patch.object(ea, "load_authorization",
                               return_value={"position_effect": "close"}), \
                mock.patch.object(ea, "validate_authorization", return_value=(True, "")), \
                mock.patch.object(ea, "reserve_authorization", return_value=True):
            ok, reason = ea.gateway(
                authorization_id="a", root=Path("."), decision={}, artifacts=[],
                strategy_id="s", symbol="AAPL", side="sell", quantity=1,
                notional=100, risk=25, broker="alpaca", account_id="p",
                environment="paper", position_effect="open")
            self.assertFalse(ok)
            self.assertIn("position_effect mismatch", reason)

    def test_strategy_ownership_fails_closed(self):
        from scripts import daytrade_engine
        class Trade:
            def __init__(self, strategy):
                self.strategy = strategy
        self.assertTrue(daytrade_engine._flatten_eligible(Trade("intraday")))
        self.assertFalse(daytrade_engine._flatten_eligible(Trade(None)))
        self.assertFalse(daytrade_engine._flatten_eligible(Trade("overnight")))

    def test_overnight_exit_propagates_failures(self):
        source = (SCRIPTS / "overnight_pilot.py").read_text(encoding="utf-8")
        self.assertIn("exit_failures.append", source)
        self.assertIn("return 1", source)

    def test_dashboard_exposes_deployment_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflow_manifest.write_manifest(
                root, task="flatten-verify", date=DATE, status="VERIFIED",
                run_id="r1", started_at="s", finished_at="f", exit_code=0,
                mode="paper")
            payload = operational_reliability.operational_reliability_payload(root, date=DATE)
        self.assertIn("deployment_readiness", payload)
        self.assertIn("research_observability", payload)
        self.assertIn("strategy_validation", payload)
        self.assertIn("uncertain_intents", payload["risk_and_broker"])

    def test_no_unauthorized_order_paths(self):
        for name in ("checkout_parity.py", "deploy_hardened_launchers.py",
                     "signal_latency_report.py", "strategy_replay_guard.py",
                     "operational_reliability.py"):
            source = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertNotIn("place_signal_order(", source, name)
            self.assertNotIn("authorize_entry_order(", source, name)

if __name__ == "__main__":
    unittest.main()
