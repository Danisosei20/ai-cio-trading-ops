import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.desk_safety_status import (
    classify_mode,
    classify_entries,
    classify_certification,
    classify_reconciliation,
    snapshot,
    PROTECTIVE_TASKS,
    ENTRY_TASKS,
    NEUTRAL_TASKS,
)


class ClassifierTests(unittest.TestCase):
    def test_current_env_is_paper_only(self):
        self.assertEqual(classify_mode({"TRADING_ENABLED": "false",
            "TRADING_MODE": "paper_auto", "PAPER_TRADING_ENABLED": "true"}), "PAPER ONLY")

    def test_live_switch_overrides_paper_mode(self):
        self.assertEqual(classify_mode({"TRADING_ENABLED": "true",
            "TRADING_MODE": "paper_auto", "PAPER_TRADING_ENABLED": "true"}), "LIVE")

    def test_live_approval_and_research_only(self):
        self.assertEqual(classify_mode({"TRADING_ENABLED": "false",
            "TRADING_MODE": "live_approval"}), "LIVE APPROVAL")
        self.assertEqual(classify_mode({"TRADING_ENABLED": "false",
            "TRADING_MODE": "research_only"}), "RESEARCH ONLY")

    def test_missing_env_is_unknown(self):
        self.assertEqual(classify_mode({}), "UNKNOWN")

    def test_all_entry_tasks_disabled_is_paused(self):
        self.assertEqual(classify_entries({n: "Disabled" for n in ENTRY_TASKS}), "PAUSED")

    def test_any_entry_task_enabled_is_active(self):
        states = {n: "Disabled" for n in ENTRY_TASKS}
        states[ENTRY_TASKS[0]] = "Enabled"
        self.assertEqual(classify_entries(states), "ACTIVE")

    def test_unknown_entry_task_fails_closed(self):
        states = {n: "Disabled" for n in ENTRY_TASKS}
        states[ENTRY_TASKS[1]] = None
        self.assertEqual(classify_entries(states), "UNKNOWN")

    def test_certification_missing_file_is_not_verified(self):
        r = classify_certification(None)
        self.assertEqual(r["status"], "NOT VERIFIED")

    def test_certification_pass_carries_timestamp(self):
        r = classify_certification({"verdict": "PASS", "generated_at": "2026-11-20T20:09:00"})
        self.assertEqual(r["status"], "PASS")
        self.assertIn("2026-11-20", r["label"])

    def test_reconciliation_counts_orphans(self):
        r = classify_reconciliation({"status": "ok", "orphan_orders": ["a", "b"]})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["orphans"], 2)

    def test_task_sets_are_disjoint_and_cover_nine(self):
        self.assertEqual(len(ENTRY_TASKS), 4)
        self.assertEqual(len(PROTECTIVE_TASKS), 3)
        self.assertEqual(len(NEUTRAL_TASKS), 2)
        self.assertEqual(len(set(ENTRY_TASKS) | set(PROTECTIVE_TASKS) | set(NEUTRAL_TASKS)), 9)


class SnapshotTests(unittest.TestCase):
    def test_default_account_probe_is_callable_without_arguments(self):
        """snapshot invokes the real default probe with no args."""
        class FakeBackend:
            def __init__(self, *args, **kwargs):
                pass

            def list_positions(self):
                return []

        with patch("robinhood_tools.alpaca_paper.AlpacaPaperBackend", FakeBackend):
            with tempfile.TemporaryDirectory() as d:
                s = snapshot(Path(d), env={}, query=lambda n: "Disabled")
        self.assertEqual(s["account"], "FLAT")

    def test_missing_files_yield_unknown_and_not_verified(self):
        with tempfile.TemporaryDirectory() as d:
            s = snapshot(Path(d), env={}, query=lambda n: "Disabled",
                         account_probe=lambda: "UNKNOWN")
        self.assertEqual(s["certification"]["status"], "NOT VERIFIED")
        self.assertEqual(s["reconciliation"]["status"], "never run")
        self.assertEqual(s["account"], "UNKNOWN")
        self.assertEqual(s["alerts"], "NOT VERIFIED")

    def test_broker_failure_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            s = snapshot(Path(d), env={"TRADING_ENABLED": "false",
                "TRADING_MODE": "paper_auto", "PAPER_TRADING_ENABLED": "true"},
                query=lambda n: "Disabled", account_probe=lambda: "UNKNOWN")
        self.assertEqual(s["account"], "UNKNOWN")
        self.assertEqual(s["mode"], "PAPER ONLY")

    def test_flat_account_and_protective_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "outputs/certification").mkdir(parents=True)
            (root / "outputs/certification/paper_certification.json").write_text(
                json.dumps({"verdict": "PASS", "generated_at": "2026-11-20T20:09:00"}))
            (root / "outputs/paper").mkdir(parents=True)
            (root / "outputs/paper/fill_reconciliation.json").write_text(
                json.dumps({"status": "ok", "orphan_orders": ["x"]}))
            s = snapshot(root, env={"TRADING_ENABLED": "false",
                "TRADING_MODE": "paper_auto", "PAPER_TRADING_ENABLED": "true"},
                query=lambda n: "Ready" if n in PROTECTIVE_TASKS else "Disabled",
                account_probe=lambda: "FLAT")
        self.assertEqual(s["account"], "FLAT")
        self.assertEqual(s["entries"], "PAUSED")
        self.assertEqual(s["certification"]["status"], "PASS")
        self.assertEqual(s["reconciliation"]["orphans"], 1)
        self.assertEqual(s["protective"]["status"], "ENABLED")
        self.assertEqual(set(s["protective"]["tasks"]), set(PROTECTIVE_TASKS))
