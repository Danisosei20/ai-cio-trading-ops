"""Static and route guards for the read-only market dashboard."""
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from scripts.agent_office import Handler

HTML = Path("web/agent-office/market.html")
CSS = Path("web/agent-office/office.css")
PY = Path("scripts/agent_office.py")
JS = Path("web/agent-office/market.js")


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

    def test_market_routes_are_read_only(self):
        src = PY.read_text(encoding="utf-8")
        self.assertIn('"/api/market"', src)
        self.assertIn('"/market"', src)
        self.assertIn('"/market.js"', src)
        self.assertNotIn("do_POST", src)

    def test_renderer_draws_levels_and_source_labels(self):
        js = JS.read_text(encoding="utf-8")
        for token in ("market-chart", "poc", "tp1", "data unavailable", "source"):
            self.assertIn(token, js)

    def test_level_colors_match_risk_semantics(self):
        # Stop-loss is red (--danger / C.down); both take-profits are green (--ok / C.up).
        js = JS.read_text(encoding="utf-8")
        self.assertIn("stop: C.down", js)
        self.assertIn("tp1: C.up", js)
        self.assertIn("tp2: C.up", js)

    def test_market_picker_present(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('id="market-symbols"', html)
        self.assertIn('<select id="market-symbols"', html)  # element is a <select>, not an input

    def test_market_picker_behavior(self):
        js = JS.read_text(encoding="utf-8")
        for token in ("/api/symbols", "market-symbols", "symbol=", "history.replaceState",
                      "market-refresh", "market-symbol"):
            self.assertIn(token, js)

    def test_market_deep_link(self):
        js = JS.read_text(encoding="utf-8")
        self.assertIn("URLSearchParams", js)


class MarketRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()

    def test_market_page_and_script_are_served(self):
        code, ctype, body = self._get("/market")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b'id="market-chart"', body)
        code, ctype, body = self._get("/market.js")
        self.assertEqual(code, 200)
        self.assertIn("text/javascript", ctype)
        self.assertIn(b"market-chart", body)

    def test_market_api_rejects_invalid_symbol(self):
        code, ctype, body = self._get("/api/market?symbol=A%3BB")
        self.assertEqual(code, 200)
        self.assertIn("application/json", ctype)
        payload = json.loads(body)
        self.assertEqual(payload, {"status": "unavailable", "reason": "invalid symbol"})

    def test_market_api_fails_closed_without_artifacts(self):
        code, _, body = self._get("/api/market?symbol=NOMARKETDATA999")
        self.assertEqual(code, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("reason", payload)
