"""Static, renderer-contract and route guards for the read-only Options page.

The page mirrors market.html's premium shell but shows the desk's locally
produced option candidates. It is strictly read-only: every value comes from a
sourced local artifact, and no write/order path exists.
"""
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from scripts.agent_office import Handler

HTML = Path("web/agent-office/options.html")
CSS = Path("web/agent-office/office.css")
PY = Path("scripts/agent_office.py")
JS = Path("web/agent-office/options.js")


class OptionsUITests(unittest.TestCase):
    def test_required_ids_present(self):
        html = HTML.read_text(encoding="utf-8")
        for ident in ("options-status", "options-candidates", "options-rejected", "options-note"):
            self.assertIn(f'id="{ident}"', html)

    def test_loads_options_script_and_office_styles(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('href="/office.css"', html)
        self.assertIn('src="/options.js"', html)

    def test_header_matches_market_brand_and_back_link(self):
        html = HTML.read_text(encoding="utf-8")
        market = Path("web/agent-office/market.html").read_text(encoding="utf-8")
        for marker in ('class="brand" href="/"', "Nextus", "Trading intelligence office"):
            self.assertIn(marker, html)
            self.assertIn(marker, market)
        self.assertIn('id="options-back" class="back-link" href="/"', html)

    def test_banner_states_candidates_only(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("Candidates only \u00b7 no orders placed", html)

    def test_two_sourced_tables(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("<table", html)
        self.assertIn('id="options-candidates"', html)
        self.assertIn('id="options-rejected"', html)
        for heading in ("Contract", "Underlying", "Side", "Strike", "Expiry",
                        "DTE", "Premium", "Delta", "EV%", "Target/stop", "Source"):
            self.assertIn(heading, html)
        for heading in ("Reason",):
            self.assertIn(heading, html)

    def test_page_reuses_the_single_teal_accent(self):
        css = CSS.read_text(encoding="utf-8")
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("--accent: #6fd6c8", css)
        self.assertNotIn("#3b82f6", html)  # trading.html's blue is not an options token

    def test_options_routes_are_read_only(self):
        src = PY.read_text(encoding="utf-8")
        self.assertIn('"/api/options"', src)
        self.assertIn('"/options"', src)
        self.assertIn('"/options.js"', src)
        self.assertNotIn("do_POST", src)

    def test_options_renderer_contract(self):
        js = JS.read_text(encoding="utf-8")
        for token in ("/api/options", "data unavailable", "source", "ev_pct",
                      "strike", "textContent", "createElement"):
            self.assertIn(token, js)
        self.assertNotIn("innerHTML", js)  # never inject markup
        self.assertNotIn("outerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)

    def test_options_renderer_has_no_animation_loop(self):
        js = JS.read_text(encoding="utf-8")
        for token in ("setInterval", "requestAnimationFrame"):
            self.assertNotIn(token, js)

    def test_options_renderer_formats_numbers_and_shows_every_source(self):
        js = JS.read_text(encoding="utf-8")
        self.assertIn("toFixed", js)  # percentages and numbers are formatted
        self.assertIn("replaceChildren", js)  # empty states clear stale rows
        self.assertIn("data.sources", js)  # every source is displayed verbatim
        self.assertIn("data.rejected", js)  # rejections get their own section


class OptionsRouteTests(unittest.TestCase):
    """HTTP-level checks: the /options page must really be served, not just
    mentioned in a source string (Task 1 review carry-forward)."""

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

    def test_options_page_is_served(self):
        code, ctype, body = self._get("/options")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b'id="options-candidates"', body)
        self.assertIn(b"Candidates only", body)

    def test_options_script_is_served(self):
        code, ctype, body = self._get("/options.js")
        self.assertEqual(code, 200)
        self.assertIn("text/javascript", ctype)
        self.assertIn(b"/api/options", body)

    def test_options_api_is_json_and_fails_closed(self):
        code, ctype, body = self._get("/api/options")
        self.assertEqual(code, 200)
        self.assertIn("application/json", ctype)
        payload = json.loads(body)
        self.assertIn(payload["status"], ("ok", "unavailable"))
        if payload["status"] == "unavailable":
            self.assertIn("reason", payload)
        else:
            self.assertIn("candidates", payload)
            self.assertIn("rejected", payload)
            self.assertIn("sources", payload)


if __name__ == "__main__":
    unittest.main()
