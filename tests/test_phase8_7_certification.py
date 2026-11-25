"""Phase 8.7: launcher certification, champion benchmark, strategy and broker contracts."""
import json
import sys
from decimal import Decimal
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from robinhood_tools.workflow_manifest import (FAILED, NO_ACTION_REQUIRED,
                                               RECONCILIATION_REQUIRED,
                                               VERIFIED, read_manifest, write_manifest)
from scripts import champion_equiv_benchmark as champion
from scripts import daily_reliability_rehearsal as rehearsal
from scripts import flatten_daytrades, flatten_verified, operational_reliability
from scripts import scout_factory_compare as compare
from scripts import workflow_runner

DATE = "2026-11-24"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class LauncherTests(unittest.TestCase):
    def test_all_launchers_capture_and_propagate_rc(self):
        launchers = sorted(SCRIPTS.glob("*.bat"))
        self.assertGreaterEqual(len(launchers), 10)
        for path in launchers:
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.name == "office_server.bat":
                continue
            self.assertIn("ERRORLEVEL", text, path.name)
            self.assertIn("exit /b", text, path.name)

    def test_cwd_guard_on_every_launcher(self):
        for path in sorted(SCRIPTS.glob("*.bat")):
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertIn("if errorlevel 1 exit /b 2", text, path.name)

    def test_overnight_launchers_stay_dry_run(self):
        text = (SCRIPTS / "overnight_enter.bat").read_text(encoding="utf-8")
        self.assertIn("--dry-run", text)
        self.assertNotIn("--execute", text)

    def test_flatten_runs_protective_close_first(self):
        text = (SCRIPTS / "flatten_1545.bat").read_text(encoding="utf-8")
        self.assertLess(text.index("flatten_daytrades.py"),
                        text.index("flatten_verified.py"))


class RunnerHardeningTests(unittest.TestCase):
    def test_wrong_interpreter_never_launches_child(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = workflow_runner.run_stage(
                root, task="wrong-venv", date=DATE, mode="test",
                command=[sys.executable, "-c", "print('should not run')"],
                timeout=5, expect_artifact=None, require_artifact=False,
                expected_python=str(root / "expected/python.exe"))
            self.assertEqual(result["status"], FAILED)
            self.assertEqual(result["exit_code"], 127)
            doc = read_manifest(root, "wrong-venv", DATE)
            self.assertFalse(doc["evidence"]["interpreter_ok"])

    def test_future_dated_artifact_rejected(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact = root / "outputs/future.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("{}", encoding="utf-8")
            future = time.time() + 3600
            os.utime(artifact, (future, future))
            result = workflow_runner.run_stage(
                root, task="future", date=DATE, mode="test",
                command=[sys.executable, "-c", "print('done')"], timeout=5,
                expect_artifact="outputs/future.json")
            self.assertEqual(result["status"], FAILED)

    def test_run_id_is_unique_per_invocation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ids = []
            for _ in range(2):
                workflow_runner.run_stage(
                    root, task="dup", date=DATE, mode="test",
                    command=[sys.executable, "-c", "pass"], timeout=5,
                    expect_artifact=None, require_artifact=False)
                ids.append(read_manifest(root, "dup", DATE)["run_id"])
            self.assertNotEqual(ids[0], ids[1])

    def test_bearer_and_slack_tokens_redacted(self):
        self.assertNotIn("BEARERSECRET",
                         workflow_runner.redact("Authorization: Bearer BEARERSECRET"))
        self.assertNotIn("xoxb-ABCDEFGHIJKL",
                         workflow_runner.redact("token xoxb-ABCDEFGHIJKL"))


class ChampionBenchmarkTests(unittest.TestCase):
    def test_config_is_live_champion(self):
        cfg = champion.CHAMPION_CONFIG
        self.assertEqual(cfg["selected_analysts"], ["market", "news"])
        self.assertEqual(cfg["max_tool_rounds"], 4)
        self.assertEqual(cfg["llm_provider"], "nvidia")
        self.assertEqual(cfg["quick_think_llm"], champion.MODEL)
        self.assertEqual(cfg["max_debate_rounds"], 1)
        self.assertEqual(cfg["max_risk_discuss_rounds"], 1)

    def test_champion_path_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                champion._guard(Path(td) / "outputs/paper/tradingagents", "run1")

    def test_child_failure_writes_terminal_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(champion.subprocess, "Popen",
                                   side_effect=OSError("spawn failed")):
                report = champion.measure(
                    ta_python=Path("python"), ta_root=Path("ta"), run_id="spawn-fail",
                    root=Path(td), symbol="NVDA", date=DATE, deadline=5)
            self.assertEqual(report["status"], "error")
            self.assertEqual(report["orders_submitted"], 0)
            path = Path(td) / "outputs/shadow/research/spawn-fail/measurement.json"
            self.assertTrue(path.exists())

    def test_benchmark_never_places_orders(self):
        source = Path(champion.__file__).read_text(encoding="utf-8")
        for token in ("place_signal_order(", "authorize_entry_order(", "AlpacaPaperBackend("):
            self.assertNotIn(token, source)


class StrategyConsistencyTests(unittest.TestCase):
    def test_long_boundary_difference_is_intentional(self):
        row = compare.compare(symbol="QQQ", price=100, vwap=99, or_high=101,
                              or_low=98, bars=12, now=DATE)
        self.assertEqual(row.scout_setup, "long_reclaim")
        self.assertEqual(row.classification, "intentional_strategy_difference")

    def test_short_boundary_difference_is_intentional(self):
        row = compare.compare(symbol="QQQ", price=100, vwap=101, or_high=105,
                              or_low=100, bars=12, now=DATE)
        self.assertEqual(row.classification, "intentional_strategy_difference")

    def test_defect_label_is_reserved_for_unexplained_deviation(self):
        # Both rules reject; no defect claim is needed.
        row = compare.compare(symbol="QQQ", price=98, vwap=101, or_high=105,
                              or_low=100, bars=12, now=DATE)
        self.assertEqual(row.factory_setup, "short_breakdown")
        source = Path(compare.__file__).read_text(encoding="utf-8")
        self.assertIn('"implementation_defect"', source)

    def test_report_has_spec_version_and_shadow_label(self):
        report = compare.comparison_report([])
        self.assertEqual(report["canonical_spec_version"], "intraday-setups/v1.0.0")
        self.assertTrue(report["shadow_only"])
        self.assertEqual(report["orders_submitted"], 0)

    def test_vwap_and_or_boundaries(self):
        self.assertEqual(compare.scout_classify(
            price=100, vwap=100, or_high=101, or_low=99, bars=12),
            ("range", "range"))
        self.assertEqual(compare.scout_classify(
            price=100, vwap=99, or_high=101, or_low=99, bars=12),
            ("long", "long_reclaim"))


def _state(positions, orders=()):
    return {"positions": list(positions), "open_orders": list(orders),
            "account_equity": "100", "clock_is_open": False,
            "queried_at": __import__("time").time(), "source": "SIMULATED"}


class FlattenContractTests(unittest.TestCase):
    def test_whole_account_flat_is_verified(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(flatten_verified, "broker_state",
                                   return_value=_state([])):
                result = flatten_verified.verify(Path(td), DATE)
            self.assertEqual(result["status"], VERIFIED)
            self.assertEqual(result["evidence"]["flat_scope"], "whole_account")

    def test_strategy_flat_with_owned_record_and_unrelated_holding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auto = root / "outputs/paper/tradingagents/QQQ" / DATE / "auto.json"
            auto.parent.mkdir(parents=True, exist_ok=True)
            auto.write_text(json.dumps({"ticker": "QQQ", "action": "buy_placed",
                                        "trade_style": "day_trade"}), encoding="utf-8")
            with mock.patch.object(flatten_verified, "broker_state",
                                   return_value=_state([{"symbol": "TSLA", "qty": 3}])):
                result = flatten_verified.verify(root, DATE)
            self.assertEqual(result["status"], RECONCILIATION_REQUIRED)
            self.assertIn("TSLA", result["evidence"]["per_symbol"])

    def test_short_position_is_reconciliation_required(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auto = root / "outputs/paper/tradingagents/QQQ" / DATE / "auto.json"
            auto.parent.mkdir(parents=True, exist_ok=True)
            auto.write_text(json.dumps({"ticker": "QQQ", "action": "sell_placed",
                                        "trade_style": "day_trade"}), encoding="utf-8")
            with mock.patch.object(flatten_verified, "broker_state",
                                   return_value=_state([{"symbol": "QQQ", "qty": -5}])):
                result = flatten_verified.verify(root, DATE)
            self.assertEqual(result["status"], RECONCILIATION_REQUIRED)
            self.assertEqual(result["evidence"]["per_symbol"]["QQQ"]["direction"], "short")

    def test_stale_snapshot_never_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            state = _state([])
            state["queried_at"] = __import__("time").time() - 120
            with mock.patch.object(flatten_verified, "broker_state",
                                   return_value=state):
                result = flatten_verified.verify(Path(td), DATE)
            self.assertEqual(result["status"], RECONCILIATION_REQUIRED)

    def test_unknown_order_state_never_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(flatten_verified, "broker_state",
                                   return_value=_state([], [{"symbol": "SPY",
                                                              "status": "weird"}])):
                result = flatten_verified.verify(Path(td), DATE)
            self.assertEqual(result["status"], RECONCILIATION_REQUIRED)

    def test_corrupt_ownership_never_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "outputs/paper/overnight.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{broken", encoding="utf-8")
            with mock.patch.object(flatten_verified, "broker_state",
                                   return_value=_state([])):
                result = flatten_verified.verify(root, DATE)
            self.assertEqual(result["status"], RECONCILIATION_REQUIRED)

    def test_options_are_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(flatten_verified, "broker_state",
                                   return_value=_state([{"symbol": "NVDA  250117C00150000",
                                                         "qty": 1}])):
                result = flatten_verified.verify(Path(td), DATE)
            self.assertEqual(result["status"], NO_ACTION_REQUIRED)
            self.assertTrue(result["evidence"]["option_positions"])

    def test_broker_outage_is_reconciliation_required(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(flatten_verified, "broker_state",
                                   side_effect=OSError("offline")):
                result = flatten_verified.verify(Path(td), DATE)
            self.assertEqual(result["status"], RECONCILIATION_REQUIRED)

    def test_close_order_side_follows_position_direction(self):
        source = Path(flatten_daytrades.__file__).read_text(encoding="utf-8")
        self.assertIn('side = "sell" if held > 0 else "buy"', source)
        self.assertIn("return 1 if failures else 0", source)

    def test_verifier_still_read_only(self):
        source = Path(flatten_verified.__file__).read_text(encoding="utf-8")
        for token in ("place_signal_order(", "submit_order(", "place_long_option("):
            self.assertNotIn(token, source)


class OvernightTests(unittest.TestCase):
    def test_execution_mode_labeled(self):
        source = (SCRIPTS / "overnight_pilot.py").read_text(encoding="utf-8")
        self.assertIn("paper_dry_run", source)
        self.assertIn("orders_submitted", source)

    def test_stale_bars_are_blocked(self):
        source = (SCRIPTS / "overnight_pilot.py").read_text(encoding="utf-8")
        self.assertIn("stale or future-dated bars", source)

    def test_missing_bars_produce_rejection_reason(self):
        source = (SCRIPTS / "overnight_pilot.py").read_text(encoding="utf-8")
        self.assertIn("missing or insufficient daily bars", source)


class RehearsalAndDashboardTests(unittest.TestCase):
    def test_all_rehearsal_scenarios(self):
        for scenario in rehearsal.SCENARIOS:
            with tempfile.TemporaryDirectory() as td:
                result = rehearsal.run(Path(td), scenario=scenario)
            self.assertTrue(result["passed"], scenario)
            self.assertEqual(result["orders_submitted"], 0)

    def test_rehearsal_failure_propagates(self):
        with tempfile.TemporaryDirectory() as td:
            result = rehearsal.run(Path(td), fail_stage="news")
        self.assertIn("16:10 news readout", result["failed_events"])
        self.assertTrue(result["passed"])

    def test_dashboard_labels_and_scheduler_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_manifest(root, task="flatten-verify", date=DATE,
                           status=NO_ACTION_REQUIRED, run_id="r1",
                           started_at="s", finished_at="f", exit_code=0,
                           mode="simulated",
                           evidence={"flat_scope": "strategy_daytrade",
                                     "execution_mode": "paper_dry_run"})
            payload = operational_reliability.operational_reliability_payload(root, date=DATE)
        self.assertEqual(payload["status"], "ok")
        self.assertIn("Operational log disabled",
                      payload["scheduler"]["logging_coverage"])
        self.assertEqual(payload["signal_integrity"]["canonical_spec_version"],
                         "intraday-setups/v1.0.0")
        self.assertEqual(payload["risk_and_broker"]["flatten_scope"],
                         "strategy_daytrade")
        self.assertIn("simulated", payload["evidence_labels"])
        self.assertEqual(payload["entry_permission"].lower().split()[0], "none")


class AdversarialFixTests(unittest.TestCase):
    def test_child_graph_error_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / "outputs/shadow/research/gh-error"
            run.mkdir(parents=True)
            (run / "progress.json").write_text(json.dumps({
                "stage": "graph_finished", "status": "error",
                "counts": {"llm_calls": 1, "tool_calls": 0, "llm_errors": 1}}),
                encoding="utf-8")

            class FakeProc:
                pid = 4242
                returncode = 0
                def communicate(self, timeout=None):
                    (run / "child_result.json").write_text(json.dumps({
                        "decision": "UNKNOWN", "error": "provider 504",
                        "config_match": True, "reports": {}, "counts": {}}),
                        encoding="utf-8")
                    return "", ""
            with mock.patch.object(champion.subprocess, "Popen", return_value=FakeProc()):
                report = champion.measure(
                    ta_python=Path("python"), ta_root=Path("ta"),
                    run_id="gh-error", root=root, symbol="NVDA", date=DATE,
                    deadline=5)
            self.assertEqual(report["status"], "error")
            self.assertFalse(report["evidence_complete"])
            self.assertEqual(report["orders_submitted"], 0)

    def test_manifest_replay_from_other_date_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_manifest(root, task="news", date=DATE, status=VERIFIED,
                           run_id="r1", started_at="s", finished_at="f",
                           exit_code=0, mode="test")
            src = root / "outputs/runs" / DATE / "news.json"
            other = root / "outputs/runs/2026-11-23"
            other.mkdir(parents=True, exist_ok=True)
            (other / "news.json").write_text(src.read_text(encoding="utf-8"),
                                             encoding="utf-8")
            self.assertIsNone(read_manifest(root, "news", "2026-11-23"))
            self.assertIsNotNone(read_manifest(root, "news", DATE))

    def test_refused_close_is_a_failure(self):
        qty, side, reason = flatten_daytrades.close_plan(
            {"ticker": "QQQ", "quantity": "N/A"}, Decimal("5"), set())
        self.assertEqual((qty, side, reason),
                         (Decimal("0"), "", "no owned quantity recorded"))
        qty, side, reason = flatten_daytrades.close_plan(
            {"ticker": "QQQ", "quantity": 3}, Decimal("5"), {("QQQ", "sell")})
        self.assertEqual(reason, "a close order is already working")
        qty, side, reason = flatten_daytrades.close_plan(
            {"ticker": "QQQ", "quantity": 3}, Decimal("-5"), set())
        self.assertEqual((qty, side, reason), (Decimal("3"), "buy", ""))

    def test_research_failure_manifest_writer_exists(self):
        source = (SCRIPTS / "intraday_loop.py").read_text(encoding="utf-8")
        self.assertIn('task="research"', source)
        self.assertIn('status=FAILED', source)
        self.assertIn('return 1', source)

    def test_overnight_counts_real_orders(self):
        source = (SCRIPTS / "overnight_pilot.py").read_text(encoding="utf-8")
        self.assertIn('"orders_submitted": open_count if not args.dry_run else 0', source)
        self.assertIn("paper_order_path_authorization_off", source)


class NoOrderTests(unittest.TestCase):
    def test_new_modules_do_not_submit_orders(self):
        for name in ("champion_equiv_benchmark.py", "daily_reliability_rehearsal.py",
                     "operational_reliability.py", "flatten_verified.py"):
            source = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertNotIn("place_signal_order(", source, name)
            self.assertNotIn("authorize_entry_order(", source, name)


if __name__ == "__main__":
    unittest.main()
