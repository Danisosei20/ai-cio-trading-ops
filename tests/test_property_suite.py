"""Property-based suite: invariants for ANY input, not an enumerated list.

Wraps scripts/property_test.py with a small case count so CI runs it in
seconds, and asserts the invariants are actually being checked (a property
suite that checks nothing would pass vacuously).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import property_test


class PropertySuiteTest(unittest.TestCase):
    def test_invariants_hold_over_generated_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "property_test.json"
            with patch.object(property_test, "OUT", out):
                code = property_test.main(["--cases", "40"])
                self.assertEqual(code, 0,
                                 "an invariant was violated - see stdout")
                payload = __import__("json").loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["violations"], 0)
        # The suite must actually exercise the properties, not pass empty.
        self.assertGreater(payload["checks"], 500,
                           "too few checks to claim coverage of the space")
        for prop in property_test.PROPERTIES:
            self.assertIn(prop, payload["properties"])

    def test_the_properties_cover_every_safety_claim(self):
        expected = {"no gate crashes", "fail-closed", "direction honesty",
                    "accounting", "exit policy", "round-trip math"}
        self.assertTrue(expected.issubset(set(property_test.PROPERTIES)),
                        f"missing properties: {expected - set(property_test.PROPERTIES)}")

    def test_a_broken_invariant_is_detected(self):
        """The suite must fail when an invariant breaks - not pass blindly."""
        original = property_test.Runner.check

        def broken(self, prop, ok, detail=""):
            original(self, prop, False, detail)  # force every check to fail

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "property_test.json"
            with patch.object(property_test, "OUT", out), \
                    patch.object(property_test.Runner, "check", broken):
                code = property_test.main(["--cases", "2"])
        self.assertEqual(code, 1, "a violated invariant must fail the run")


if __name__ == "__main__":
    unittest.main()