"""Desk UI server tests (ephemeral port + scratch DB; paper DB untouched)."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.desk_server import Handler  # noqa: E402


def call(port: int, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read() or b"{}"
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {}


class DeskServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.db_path = str(Path(cls.temp.name) / "ui.db")

        class BoundHandler(Handler):
            database_path = cls.db_path

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), BoundHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.temp.cleanup()

    def test_root_serves_app(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("AI Trading Desk", resp.read().decode())

    def test_pages_and_static(self):
        for path in ("/desk", "/triggers", "/backtests", "/positions", "/logs", "/health",
                     "/static/app.js", "/static/styles.css"):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}",
                                        timeout=10) as resp:
                self.assertEqual(resp.status, 200, path)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/",
                                    timeout=10) as resp:
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            self.assertIn("Content-Security-Policy", resp.headers)

    def test_health_fresh_db_not_killed(self):
        code, body = call(self.port, "GET", "/api/health")
        self.assertEqual(code, 200)
        self.assertFalse(body["live_enabled"])
        self.assertFalse(body["killed"])
        self.assertEqual(body["exec_policy"], "OBSERVE")

    def test_kill_requires_confirmation(self):
        code, body = call(self.port, "POST", "/api/kill", {})
        self.assertEqual(code, 422)
        code, body = call(self.port, "GET", "/api/health")
        self.assertFalse(body["killed"])

    def test_kill_resume_cycle(self):
        code, body = call(self.port, "POST", "/api/kill", {"confirm": True})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        code, body = call(self.port, "GET", "/api/health")
        self.assertTrue(body["killed"])
        code, body = call(self.port, "POST", "/api/resume", {"confirm": True})
        self.assertTrue(body["ok"])
        code, body = call(self.port, "GET", "/api/health")
        self.assertFalse(body["killed"])

    def test_read_endpoints_shape(self):
        for path in ("/api/desk", "/api/triggers", "/api/backtests", "/api/research",
                     "/api/log", "/api/account"):
            code, body = call(self.port, "GET", path)
            self.assertEqual(code, 200, path)

    def test_resume_requires_confirmation_and_strict_true(self):
        code, _ = call(self.port, "POST", "/api/resume", {})
        self.assertEqual(code, 422)
        code, _ = call(self.port, "POST", "/api/resume", {"confirm": "true"})
        self.assertEqual(code, 422)
        code, _ = call(self.port, "POST", "/api/resume", {"confirm": 1})
        self.assertEqual(code, 422)

    def test_kill_touches_only_flag(self):
        code, before = call(self.port, "GET", "/api/account")
        call(self.port, "POST", "/api/kill", {"confirm": True})
        code, after = call(self.port, "GET", "/api/account")
        self.assertEqual(code, 200)
        # Live marks move; compare structure, not prices.
        def shape(d):
            return (d.get("ok"),
                    sorted((p.get("symbol"), p.get("qty")) for p in d.get("positions", [])),
                    len(d.get("orders", [])))
        self.assertEqual(shape(before), shape(after))
        call(self.port, "POST", "/api/resume", {"confirm": True})

    def test_no_secret_material_in_endpoints(self):
        import json as _json

        blob = ""
        for path in ("/api/health", "/api/account", "/api/desk", "/api/backtests",
                     "/api/research", "/api/log"):
            code, body = call(self.port, "GET", path)
            self.assertEqual(code, 200, path)
            blob += _json.dumps(body)
        lowered = blob.lower()
        # Token formats only: the log legitimately names variables
        # (e.g. FRED_API_KEY guidance) but must never carry values.
        for token in ("xoxb-", "xoxp-", "begin private", "akia"):
            self.assertNotIn(token, lowered, token)

    def test_unknown_and_bad_json(self):
        code, _ = call(self.port, "GET", "/api/nope")
        self.assertEqual(code, 404)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/kill",
                                     data=b"{bad", method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


if __name__ == "__main__":
    unittest.main()
