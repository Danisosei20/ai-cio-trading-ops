import unittest

from scripts.desk_safety_status import (
    classify_mode,
    classify_entries,
    classify_certification,
    classify_reconciliation,
    ENTRY_TASKS,
    PROTECTIVE_TASKS,
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
