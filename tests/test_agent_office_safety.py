"""Safety route tests: read-only, loopback, no secrets."""
from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from scripts.agent_office import Handler


class SafetyRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _get(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}", timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")

    def test_safety_route_shape(self):
        code, body = self._get("/api/safety")
        self.assertEqual(code, 200)
        for key in ("entries", "mode", "account", "certification",
                    "reconciliation", "protective", "alerts", "generated_at"):
            self.assertIn(key, body)

    def test_status_merges_safety(self):
        code, body = self._get("/api/status")
        self.assertEqual(code, 200)
        self.assertIn("safety", body)
        self.assertIn("session", body)

    def test_safety_payload_has_no_secrets(self):
        _, body = self._get("/api/safety")
        blob = json.dumps(body).lower()
        for token in ("xoxb-", "xoxp-", "begin private", "akia", "alpaca_api_key"):
            self.assertNotIn(token, blob)

    def test_no_write_route_added(self):
        from urllib.error import HTTPError

        for path in ("/api/safety", "/api/status"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}{path}", data=b"{}", method="POST")
            with self.assertRaises(HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=10)
            self.assertIn(ctx.exception.code, (404, 405, 501))
