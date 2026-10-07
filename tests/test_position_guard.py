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


if __name__ == "__main__":
    unittest.main()
