from __future__ import annotations

import unittest

from scripts.secret_scan import contains_potential_secret


class SecretScanTests(unittest.TestCase):
    def test_empty_example_key_does_not_consume_the_next_line(self):
        public_name = "ALPACA_API_" + "KEY"
        private_name = "ALPACA_SECRET_" + "KEY"
        self.assertFalse(contains_potential_secret(f"{public_name}=\n{private_name}=\n"))

    def test_non_placeholder_secret_assignment_is_detected(self):
        key_name = "API_" + "KEY"
        self.assertTrue(contains_potential_secret(f"{key_name}=not-a-real-secret\n"))
        self.assertFalse(contains_potential_secret(f"{key_name}=$LOCAL_SECRET\n"))


if __name__ == "__main__":
    unittest.main()
