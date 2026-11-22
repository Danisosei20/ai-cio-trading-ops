"""Guards for the market chart's level-label clarity.

The visual result is verified in a browser; these static guards stop the
renderer regressing to overlapping two-line labels or an unseparated
multi-session chart.
"""
import unittest
from pathlib import Path


class ChartClarityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = Path("web/agent-office/market.js").read_text(encoding="utf-8")

    def test_levels_share_a_price_are_merged(self):
        self.assertIn("grouped", self.js)
        self.assertIn("join('/')", self.js)

    def test_single_line_labels_replace_stacked_name_and_value(self):
        self.assertIn("LEVEL_STYLE", self.js)
        self.assertIn("LABEL_FONT", self.js)
        self.assertNotIn("LEVEL_COLOR", self.js)
        self.assertNotIn("labelY + 10", self.js)

    def test_labels_use_a_wider_gutter_and_collision_avoidance(self):
        self.assertIn("padR = 152", self.js)
        self.assertIn("rowH", self.js)
        self.assertIn("const overflow", self.js)

    def test_multi_session_chart_marks_session_boundaries(self):
        self.assertIn("Session boundaries", self.js)
        self.assertIn("priorDate", self.js)
