"""Static guards for the read-only market dashboard page markup."""
import unittest
from pathlib import Path

HTML = Path("web/agent-office/market.html")
CSS = Path("web/agent-office/office.css")


class MarketUITests(unittest.TestCase):
    def test_required_ids_present(self):
        html = HTML.read_text(encoding="utf-8")
        for ident in ("market-symbol", "market-chart", "market-levels", "market-status", "market-note", "market-back"):
            self.assertIn(f'id="{ident}"', html)

    def test_loads_market_script_and_office_styles(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('href="/office.css"', html)
        self.assertIn('src="/market.js"', html)

    def test_chart_is_a_canvas(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('<canvas id="market-chart"', html)

    def test_narrow_screen_stacks_chart_above_levels(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 740px)", css)
        self.assertIn("market-chart-panel", css)
        self.assertIn("market-levels-panel", css)

    def test_page_reuses_the_single_teal_accent(self):
        css = CSS.read_text(encoding="utf-8")
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("--accent: #6fd6c8", css)
        self.assertNotIn("#3b82f6", html)  # trading.html's blue is not a market token
