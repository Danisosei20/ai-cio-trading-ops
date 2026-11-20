from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from robinhood_tools.desk_policy import (
    POLICY,
    policy_snapshot,
    policy_status,
)


class DeskPolicyContractTest(unittest.TestCase):
    def test_finalized_options_values_are_shared(self):
        self.assertEqual(POLICY.quoted_iv_cap, 0.45)
        self.assertEqual(POLICY.guard_stop_pct, 0.25)
        self.assertEqual(POLICY.modeled_reward_pct, 0.50)
        self.assertEqual(POLICY.minimum_reward_risk, 2.0)
        self.assertEqual(POLICY.max_vix, 25.0)
        self.assertTrue(POLICY.unlimited_winners)
        self.assertTrue(POLICY.paper_only)

    def test_snapshot_is_json_safe_and_has_canonical_paths(self):
        snapshot = policy_snapshot()
        self.assertEqual(snapshot["policy_version"], POLICY.version)
        self.assertEqual(snapshot["options"]["quoted_iv_cap"], 0.45)
        self.assertTrue(snapshot["paper_only"])
        self.assertTrue(snapshot["paths"]["options_guard"].endswith("options_guard.json"))

    def test_non_paper_runtime_is_blocked(self):
        status = policy_status(paper_safe=False)
        self.assertEqual(status["status"], "BLOCKED")
        self.assertIn("paper", " ".join(status["reasons"]).lower())

    def test_stale_source_is_degraded(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        status = policy_status(paper_safe=True, source_times={"market": old})
        self.assertEqual(status["status"], "DEGRADED")
        self.assertIn("market", status["stale_sources"])


if __name__ == "__main__":
    unittest.main()
