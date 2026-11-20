from pathlib import Path
import unittest

from robinhood_tools.desk_policy import POLICY


PIPELINE = (Path(__file__).resolve().parents[1] / "web" / "agent-office" / "pipeline.html").read_text(encoding="utf-8")


class PipelinePolicyConsistencyTest(unittest.TestCase):
    def test_pipeline_uses_active_exit_and_reward_policy(self):
        self.assertNotIn("+2% take", PIPELINE)
        self.assertNotIn("1.5:1", PIPELINE)
        self.assertIn("-25% guard stop", PIPELINE)
        self.assertIn("2:1 R:R min", PIPELINE)
        self.assertIn("winners ride", PIPELINE)

    def test_contract_values_are_represented(self):
        self.assertIn(f"{POLICY.guard_stop_pct:.0%}", PIPELINE)
        self.assertIn(f"{POLICY.minimum_reward_risk:.0f}:1", PIPELINE)


if __name__ == "__main__":
    unittest.main()
