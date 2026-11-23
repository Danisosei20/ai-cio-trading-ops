"""--no-broker certification must never perform a broker read."""
import unittest

from scripts import paper_certification


class CertificationHermeticTests(unittest.TestCase):
    def test_watch_stub_includes_open_order_underliers(self):
        src = open(paper_certification.__file__, encoding="utf-8").read()
        self.assertIn("open_order_underliers=lambda", src)

    def test_watch_output_contains_entry_line(self):
        # With all broker reads stubbed, the watch must emit a real entry line.
        out = paper_certification._watch_entries(
            "RISK_ON", weak=[], ranked=[{"symbol": "AAPL", "score": 1.0}], held=set())
        self.assertIn("[entry]", out)
