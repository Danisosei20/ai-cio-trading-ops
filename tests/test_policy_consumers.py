from __future__ import annotations

import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path

from robinhood_tools.desk_policy import POLICY
from robinhood_tools.option_entry_risk import MAX_QUOTED_IV, MAX_VIX
from robinhood_tools.options_gate import MODELED_REWARD_PCT, MIN_REWARD_RISK


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PolicyConsumerTest(unittest.TestCase):
    def test_entry_and_odds_consumers_use_shared_values(self):
        self.assertEqual(MAX_QUOTED_IV, POLICY.quoted_iv_cap)
        self.assertEqual(MAX_VIX, POLICY.max_vix)
        self.assertEqual(MODELED_REWARD_PCT, POLICY.modeled_reward_pct)
        self.assertEqual(MIN_REWARD_RISK, POLICY.minimum_reward_risk)

    def test_options_guard_default_stop_uses_shared_policy(self):
        guard = load_script("options_guard")
        self.assertEqual(guard.DEFAULT_STOP_PCT, Decimal(str(POLICY.guard_stop_pct)))

    def test_paper_session_report_defaults_use_shared_policy(self):
        report = load_script("paper_session_report")
        self.assertEqual(report.DEFAULT_MAX_IV, POLICY.quoted_iv_cap)
        self.assertEqual(report.DEFAULT_REWARD_PCT, POLICY.modeled_reward_pct)
        self.assertEqual(report.DEFAULT_STOP_PCT, POLICY.guard_stop_pct)


if __name__ == "__main__":
    unittest.main()
