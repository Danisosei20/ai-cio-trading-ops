from __future__ import annotations

import unittest
from math import nan
from decimal import Decimal

from robinhood_tools.errors import PolicyViolation
from robinhood_tools.option_entry_risk import validate_option_entry, validate_quoted_iv


class OptionEntryRiskTests(unittest.TestCase):
    def test_missing_quoted_iv_blocks_entry(self):
        with self.assertRaisesRegex(PolicyViolation, "quoted IV unavailable"):
            validate_quoted_iv(None)

    def test_quoted_iv_at_45_percent_is_allowed(self):
        validate_quoted_iv(0.45)

    def test_quoted_iv_above_45_percent_blocks_entry(self):
        with self.assertRaisesRegex(PolicyViolation, "quoted IV 45.1% exceeds 45.0%"):
            validate_quoted_iv(0.451)

    def test_non_finite_quoted_iv_blocks_entry(self):
        with self.assertRaisesRegex(PolicyViolation, "quoted IV unavailable"):
            validate_quoted_iv(nan)

    def test_vix_above_25_blocks_new_entry(self):
        with self.assertRaisesRegex(PolicyViolation, "VIX 25.1.*above 25"):
            validate_option_entry(vix=25.1, premium=Decimal("400"),
                                  contracts=1, realized_pnl_today=Decimal("0"))

    def test_missing_vix_blocks_new_entry(self):
        with self.assertRaisesRegex(PolicyViolation, "VIX unavailable"):
            validate_option_entry(vix=None, premium=Decimal("400"),
                                  contracts=1, realized_pnl_today=Decimal("0"))

    def test_premium_above_500_blocks_new_entry(self):
        with self.assertRaisesRegex(PolicyViolation, "premium.*500"):
            validate_option_entry(vix=20, premium=Decimal("500.01"),
                                  contracts=1, realized_pnl_today=Decimal("0"))

    def test_more_than_one_contract_blocks_new_entry(self):
        with self.assertRaisesRegex(PolicyViolation, "contracts.*1"):
            validate_option_entry(vix=20, premium=Decimal("400"),
                                  contracts=2, realized_pnl_today=Decimal("0"))

    def test_daily_loss_of_500_blocks_new_entry(self):
        with self.assertRaisesRegex(PolicyViolation, "daily loss.*500"):
            validate_option_entry(vix=20, premium=Decimal("400"),
                                  contracts=1, realized_pnl_today=Decimal("-500"))

    def test_exposure_inside_all_limits_is_allowed(self):
        validate_option_entry(vix=20, premium=Decimal("500"), contracts=1,
                              realized_pnl_today=Decimal("-499.99"))


if __name__ == "__main__":
    unittest.main()
