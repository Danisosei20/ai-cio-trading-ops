"""Static, renderer-contract and route guards for the read-only News page.

The page mirrors market.html's premium shell but shows only the AI-generated
explanations produced by scripts/news_explainer.py. It is strictly read-only:
every value comes from a sourced local artifact, citations link back to the
fetched articles, no trade recommendation is shown and no write path exists.
"""
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from scripts.agent_office import Handler

HTML = Path("web/agent-office/news.html")
CSS = Path("web/agent-office/office.css")
PY = Path("scripts/agent_office.py")
JS = Path("web/agent-office/news.js")


class NewsUITests(unittest.TestCase):
    def test_required_ids_present(self):
        html = HTML.read_text(encoding="utf-8")
        for ident in ("news-status", "news-list", "news-note"):
            self.assertIn(f'id="{ident}"', html)

    def test_loads_news_script_and_office_styles(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('href="/office.css"', html)
        self.assertIn('src="/news.js"', html)

    def test_header_matches_market_brand_and_back_link(self):
        html = HTML.read_text(encoding="utf-8")
        market = Path("web/agent-office/market.html").read_text(encoding="utf-8")
        for marker in ('class="brand" href="/"', "Nextus", "Trading intelligence office"):
            self.assertIn(marker, html)
            self.assertIn(marker, market)
        self.assertIn('id="news-back" class="back-link" href="/"', html)

    def test_banner_states_read_only_no_recommendations(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("AI news analysis \u00b7 sources cited \u00b7 no trade recommendations", html)

    def test_page_reuses_the_single_teal_accent(self):
        css = CSS.read_text(encoding="utf-8")
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("--accent: #6fd6c8", css)
        self.assertNotIn("#3b82f6", html)  # trading.html's blue is not a news token

    def test_news_routes_are_read_only_and_not_duplicated(self):
        src = PY.read_text(encoding="utf-8")
        self.assertIn('"/api/news"', src)
        self.assertIn('"/news"', src)
        self.assertIn('"/news.js"', src)
        self.assertNotIn("do_POST", src)
        self.assertEqual(src.count('elif route == "/news":'), 1)

    def test_news_renderer_contract(self):
        js = JS.read_text(encoding="utf-8")
        for token in ("/api/news", "data unavailable", "createElement",
                      "textContent", "source", "cite"):
            self.assertIn(token, js)
        for token in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
            self.assertNotIn(token, js)  # never inject markup

    def test_news_renderer_has_no_animation_loop(self):
        js = JS.read_text(encoding="utf-8")
        for token in ("setInterval", "requestAnimationFrame"):
            self.assertNotIn(token, js)

    def test_fetch_is_bounded_and_fails_closed(self):
        js = JS.read_text(encoding="utf-8")
        self.assertIn("AbortSignal.timeout", js)
        self.assertIn("fetch('/api/news'", js)
        self.assertIn("data.status !== 'ok'", js)
        self.assertIn("replaceChildren", js)  # empty states clear stale rows

    def test_citation_href_is_restricted_to_http_https(self):
        js = JS.read_text(encoding="utf-8")
        self.assertIn("^https?://", js)
        self.assertIn("url.protocol === 'http:' || url.protocol === 'https:'", js)
        self.assertIn("noopener noreferrer", js)


class NewsRouteTests(unittest.TestCase):
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

    def test_news_page_is_served(self):
        code, ctype, body = self._get("/news")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b'id="news-list"', body)
        self.assertIn(b"no trade recommendations", body)

    def test_news_script_is_served(self):
        code, ctype, body = self._get("/news.js")
        self.assertEqual(code, 200)
        self.assertIn("text/javascript", ctype)
        self.assertIn(b"/api/news", body)

    def test_news_api_is_json_and_fails_closed(self):
        code, ctype, body = self._get("/api/news")
        self.assertEqual(code, 200)
        self.assertIn("application/json", ctype)
        payload = json.loads(body)
        self.assertIn(payload["status"], ("ok", "unavailable"))
        if payload["status"] == "unavailable":
            self.assertIn("reason", payload)
        else:
            for key in ("rows", "sources", "generated_at"):
                self.assertIn(key, payload)
            for row in payload["rows"]:
                for key in ("symbol", "move_pct", "explanation", "confidence",
                            "citations", "sources"):
                    self.assertIn(key, row)


if __name__ == "__main__":
    unittest.main()
