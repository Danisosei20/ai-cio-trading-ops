#!/usr/bin/env python3
"""Read-only local office viewer. Run: python3 scripts/agent_office.py"""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.office_state import snapshot


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route == "/api/status":
            body = json.dumps(snapshot(ROOT)).encode()
            mime = "application/json"
        elif route in ("/", "/office.js", "/office.css", "/scene.js"):
            name = "index.html" if route == "/" else route[1:]
            body = (ROOT / "web/agent-office" / name).read_bytes()
            mime = {"html": "text/html", "js": "text/javascript", "css": "text/css"}[name.rsplit(".", 1)[1]]
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Agent office: http://localhost:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
