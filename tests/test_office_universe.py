"""Static guards for the read-only Office loaded-universe strip."""
import re
import unittest
from pathlib import Path

BASE = Path("web/agent-office")


class OfficeUniverseTests(unittest.TestCase):
    def setUp(self):
        self.html = (BASE / "index.html").read_text(encoding="utf-8")
        self.js = (BASE / "office.js").read_text(encoding="utf-8")
        self.css = (BASE / "office.css").read_text(encoding="utf-8")

    def test_universe_markup(self):
        self.assertIn('id="universe-list"', self.html)
        self.assertIn('id="universe-count"', self.html)

    def test_universe_behavior(self):
        for token in ("/api/symbols", "/market?symbol=", "No sourced symbols yet"):
            self.assertIn(token, self.js)

    def test_universe_symbol_list_is_filtered(self):
        self.assertIn(r"/^[A-Z0-9._-]{1,8}$/", self.js)

    def test_universe_no_new_color(self):
        self.assertIn("/* Universe */", self.css)
        start = self.css.index("/* Universe */")
        end = self.css.index("/*", start + len("/* Universe */"))
        block = self.css[start:end]
        hex_colors = re.findall(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b", block)
        self.assertEqual(hex_colors, [])
