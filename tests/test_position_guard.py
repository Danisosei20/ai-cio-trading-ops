"""Position guard logic tests (pure; no network/broker)."""
from __future__ import annotations

import unittest
from decimal import Decimal

from robinhood_tools.errors import PolicyViolation
from robinhood_tools.position_guard import GuardPolicy, evaluate


class GuardLogicTest(unittest.TestCase):
    def test_hold_between_levels(self):
        policy = GuardPolicy()
        ev = evaluate(Decimal("237.00"), Decimal("237.13"), policy)
        self.assertEqual(ev.signal, "HOLD")
        self.assertEqual(ev.stop_price, Decimal("237.13") * Decimal("0.92"))
        self.assertEqual(ev.target_price, Decimal("237.13") * Decimal("1.20"))

    def test_stop_trigger(self):
        ev = evaluate(Decimal("210.00"), Decimal("237.13"), GuardPolicy())
        self.assertEqual(ev.signal, "STOP")

    def test_target_trigger(self):
        ev = evaluate(Decimal("290.00"), Decimal("237.13"), GuardPolicy())
        self.assertEqual(ev.signal, "TARGET")

    def test_exact_boundary_triggers(self):
        policy = GuardPolicy(stop_pct=Decimal("0.10"), target_pct=Decimal("0.10"))
        self.assertEqual(evaluate(Decimal("90"), Decimal("100"), policy).signal, "STOP")
        self.assertEqual(evaluate(Decimal("110"), Decimal("100"), policy).signal, "TARGET")

    def test_bad_policy_rejected(self):
        with self.assertRaises(PolicyViolation):
            GuardPolicy(stop_pct=Decimal("0"))
        with self.assertRaises(PolicyViolation):
            GuardPolicy(target_pct=Decimal("1.5"))

    def test_bad_prices_rejected(self):
        with self.assertRaises(PolicyViolation):
            evaluate(Decimal("0"), Decimal("100"), GuardPolicy())


class DailyTargetTest(unittest.TestCase):
    def test_bank_at_target(self):
        from decimal import Decimal

        from robinhood_tools.daily_target import day_pnl, evaluate

        self.assertEqual(day_pnl(Decimal("1.0"), Decimal("1.001")), Decimal("0.001"))
        status = evaluate(Decimal("100"))
        self.assertTrue(status.halted)
        status = evaluate(Decimal("150"))
        self.assertTrue(status.halted)

    def test_stop_the_bleed(self):
        from decimal import Decimal

        from robinhood_tools.daily_target import evaluate

        status = evaluate(Decimal("-50"))
        self.assertTrue(status.halted)
        self.assertIn("loss", status.reason)

    def test_open_day(self):
        from decimal import Decimal

        from robinhood_tools.daily_target import evaluate

        status = evaluate(Decimal("25"))
        self.assertFalse(status.halted)


if __name__ == "__main__":
    unittest.main()
