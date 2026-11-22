"""Static, renderer-contract and route guards for the read-only Basket page.

The page mirrors market.html's premium shell but shows the desk's signal-only
$500 short-basket proposal. It is strictly read-only: every value comes from a
sourced local artifact, no write/order path exists, and the banner says plainly
that no orders were placed.
"""
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from scripts.agent_office import Handler

HTML = Path("web/agent-office/basket.html")
CSS = Path("web/agent-office/office.css")
PY = Path("scripts/agent_office.py")
JS = Path("web/agent-office/basket.js")


class BasketUITests(unittest.TestCase):
    def test_required_ids_present(self):
        html = HTML.read_text(encoding="utf-8")
        for ident in ("basket-status", "basket-rows", "basket-note"):
            self.assertIn(f'id="{ident}"', html)

    def test_loads_basket_script_and_office_styles(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('href="/office.css"', html)
        self.assertIn('src="/basket.js"', html)

    def test_header_matches_market_brand_and_back_link(self):
        html = HTML.read_text(encoding="utf-8")
        market = Path("web/agent-office/market.html").read_text(encoding="utf-8")
        for marker in ('class="brand" href="/"', "Nextus", "Trading intelligence office"):
            self.assertIn(marker, html)
            self.assertIn(marker, market)
        self.assertIn('id="basket-back" class="back-link" href="/"', html)

    def test_banner_states_signal_only(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("Signal only \u2014 no orders placed", html)

    def test_proposal_table_has_sourced_columns(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("<table", html)
        self.assertIn('id="basket-rows"', html)
        for heading in ("Symbol", "Reference", "Change %", "Allocation", "Shares", "Notional", "Source"):
            self.assertIn(heading, html)
        self.assertIn('colspan="7"', html)

    def test_page_reuses_the_single_teal_accent(self):
        css = CSS.read_text(encoding="utf-8")
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("--accent: #6fd6c8", css)
        self.assertNotIn("#3b82f6", html)  # trading.html's blue is not a basket token

    def test_basket_routes_are_read_only(self):
        src = PY.read_text(encoding="utf-8")
        self.assertIn('"/api/basket"', src)
        self.assertIn('"/basket"', src)
        self.assertIn('"/basket.js"', src)
        self.assertNotIn("do_POST", src)
        # Task 1 already registered /basket — never add a second branch.
        self.assertEqual(src.count('elif route == "/basket":'), 1)

    def test_basket_renderer_contract(self):
        js = JS.read_text(encoding="utf-8")
        for token in ("/api/basket", "data unavailable", "source", "signal only",
                      "textContent", "createElement"):
            self.assertIn(token, js)
        self.assertNotIn("innerHTML", js)  # never inject markup
        self.assertNotIn("outerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)

    def test_basket_renderer_has_no_animation_loop(self):
        js = JS.read_text(encoding="utf-8")
        for token in ("setInterval", "requestAnimationFrame"):
            self.assertNotIn(token, js)

    def test_fetch_is_bounded_and_fails_closed(self):
        js = JS.read_text(encoding="utf-8")
        self.assertIn("AbortSignal.timeout", js)
        self.assertIn("fetch('/api/basket'", js)
        self.assertIn("data.status !== 'ok'", js)
        self.assertIn("replaceChildren", js)  # empty states clear stale rows

    def test_numbers_formatted_and_every_source_shown(self):
        js = JS.read_text(encoding="utf-8")
        self.assertIn("toFixed", js)
        self.assertIn("data.sources", js)  # every source is displayed verbatim
        self.assertIn("data.capital", js)
        self.assertIn("notional", js)


class BasketRouteTests(unittest.TestCase):
    """HTTP-level checks: the page and script must really be served, not just
    mentioned in a source string."""

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

    def test_basket_page_is_served(self):
        code, ctype, body = self._get("/basket")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b'id="basket-rows"', body)
        self.assertIn(b"Signal only", body)

    def test_basket_script_is_served(self):
        code, ctype, body = self._get("/basket.js")
        self.assertEqual(code, 200)
        self.assertIn("text/javascript", ctype)
        self.assertIn(b"/api/basket", body)

    def test_basket_api_is_json_and_fails_closed(self):
        code, ctype, body = self._get("/api/basket")
        self.assertEqual(code, 200)
        self.assertIn("application/json", ctype)
        payload = json.loads(body)
        self.assertIn(payload["status"], ("ok", "unavailable"))
        if payload["status"] == "unavailable":
            self.assertIn("reason", payload)
        else:
            for key in ("capital", "count", "basket", "sources", "generated_at"):
                self.assertIn(key, payload)
            for row in payload["basket"]:
                for key in ("symbol", "reference_price", "change_pct", "allocation",
                            "shares", "notional", "source"):
                    self.assertIn(key, row)


if __name__ == "__main__":
    unittest.main()
