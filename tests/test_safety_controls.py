from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.reporting import MonthlyMetrics, export_audit_bundle, monthly_report
from robinhood_tools.risk import PortfolioState, RiskLimits
from robinhood_tools.safety import DataQuality, classify_market_regime, require_earnings_clear, require_regime_hurdle


class SafetyControlsTests(unittest.TestCase):
    def test_small_account_limits_order_cash_and_position_count(self):
        limits = RiskLimits(max_order_value=Decimal("25"), min_cash_dollars=Decimal("50"), max_open_positions=1)
        base = dict(equity=Decimal("100"), cash=Decimal("100"), position_weights={}, sector_weights={},
                    pending_approvals=0, approved_capital_today=Decimal("0"))
        with self.assertRaisesRegex(PolicyViolation, "maximum order"):
            limits.validate_purchase(PortfolioState(**base), symbol="AAPL", sector="Tech",
                                     order_value=Decimal("26"), avg_daily_dollar_volume=Decimal("1000000"))
        occupied = PortfolioState(**base, open_positions=1)
        with self.assertRaisesRegex(PolicyViolation, "open positions"):
            limits.validate_purchase(occupied, symbol="AAPL", sector="Tech",
                                     order_value=Decimal("10"), avg_daily_dollar_volume=Decimal("1000000"))

    def test_loss_limits_disable_new_purchases(self):
        portfolio = PortfolioState(Decimal("100"), Decimal("100"), {}, {}, 0, Decimal("0"),
                                   realized_pnl_today=Decimal("-5"))
        with self.assertRaisesRegex(PolicyViolation, "Daily loss"):
            RiskLimits(max_daily_loss=Decimal("5")).validate_purchase(
                portfolio, symbol="AAPL", sector="Tech", order_value=Decimal("1"),
                avg_daily_dollar_volume=Decimal("1000000"),
            )

    def test_data_regime_and_earnings_gates_fail_closed(self):
        incomplete = DataQuality(True, True, True, True, False, True)
        with self.assertRaisesRegex(PolicyViolation, "incomplete"):
            incomplete.require_complete()
        regime = classify_market_regime(spy_above_200d=False, volatility_pct=Decimal("0.35"),
                                        breadth_pct=Decimal("0.30"), credit_stress=False)
        self.assertEqual(regime, "risk_off")
        with self.assertRaises(PolicyViolation):
            require_regime_hurdle(regime, score=96)
        with self.assertRaises(PolicyViolation):
            require_earnings_clear(today=date.today(), earnings_date=date.today() + timedelta(days=3))

    def test_emergency_kill_and_symbol_cooldown_are_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            db.set_emergency_kill(True)
            with self.assertRaisesRegex(PolicyViolation, "Emergency"):
                CioDatabase(db.path).require_not_killed()
            db.set_emergency_kill(False)
            db.add_symbol_cooldown("AAPL", reason="thesis invalidated", starts_on="2026-07-10", expires_on="2026-07-17")
            with self.assertRaisesRegex(PolicyViolation, "cooldown"):
                db.require_no_symbol_cooldown("AAPL", today="2026-07-12")

    def test_monthly_report_and_checksummed_audit_export(self):
        metrics = MonthlyMetrics(Decimal("0.03"), Decimal("0.02"), Decimal("-0.01"), Decimal("0.6"),
                                 Decimal("1.5"), Decimal("0.001"), Decimal("0.7"), 4, 2, 0)
        report = monthly_report(metrics)
        self.assertEqual(report["excess_return"], "0.01")
        with tempfile.TemporaryDirectory() as directory:
            output, digest = export_audit_bundle(report, Path(directory) / "audit.json")
            self.assertTrue(output.with_suffix(".json.sha256").exists())
            self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
