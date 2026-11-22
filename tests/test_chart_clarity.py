"""Guards for the TradingView-style market chart renderer.

The visual result is verified in a browser; these static guards stop the
renderer regressing to a cramped gutter, overlapping labels or a missing
crosshair / price axis.
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
        self.assertNotIn("padR = 152", self.js)

    def test_labels_use_collision_avoidance(self):
        self.assertIn("rowH", self.js)
        self.assertIn("const overflow", self.js)
        self.assertIn("chipW", self.js)

    def test_price_scale_follows_bars_with_edge_markers_for_far_levels(self):
        self.assertIn("offView", self.js)
        self.assertIn("edgeGroups", self.js)
        self.assertIn("range * 0.08", self.js)


class TradingViewSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = Path("web/agent-office/market.js").read_text(encoding="utf-8")

    def test_right_price_axis_and_bottom_time_axis(self):
        self.assertIn("axisW", self.js)
        self.assertIn("plotRight", self.js)
        self.assertIn("timeH", self.js)
        self.assertIn("fillRect(0, h - timeH", self.js)

    def test_session_dates_and_time_ticks_do_not_collide(self):
        self.assertIn("sessionX", self.js)
        self.assertIn("h - 19", self.js)
        self.assertIn("Math.abs(s.x - gx) < 44", self.js)

    def test_crosshair_guides_and_axis_boxes(self):
        self.assertIn("mousemove", self.js)
        self.assertIn("mouseleave", self.js)
        self.assertIn("TV.cross", self.js)
        self.assertIn("setLineDash([4, 4])", self.js)

    def test_legend_shows_sourced_ohlc_and_change(self):
        self.assertIn("'O ' + fmt(o)", self.js)
        self.assertIn("change.toFixed(2)", self.js)

    def test_read_only_renderer_has_no_write_call(self):
        self.assertNotIn("method: 'POST'", self.js)
        self.assertNotIn("do_POST", self.js)
