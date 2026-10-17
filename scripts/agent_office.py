#!/usr/bin/env python3
"""Read-only local office viewer. Run: python3 scripts/agent_office.py"""
import argparse
import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.office_state import snapshot  # noqa: E402

ET = ZoneInfo("America/New_York")
SESSION_START = "10:15"
SESSION_END = "15:45"


def trading_window() -> dict:
    """Whether the desk's trading window (10:15-15:45 ET weekdays) is open."""
    now = datetime.now(ET)
    start = datetime.strptime(SESSION_START, "%H:%M").time()
    end = datetime.strptime(SESSION_END, "%H:%M").time()
    open_window = now.weekday() < 5 and start <= now.time() <= end
    return {
        "window": f"{SESSION_START}-{SESSION_END} ET",
        "open": open_window,
        "weekday": now.strftime("%A"),
        "now_et": now.isoformat(),
        "note": "Trading is gated to this window; outside it the desk researches only.",
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route == "/api/status":
            body = json.dumps({**snapshot(ROOT), "session": trading_window()}).encode()
            mime = "application/json"
        elif route == "/api/session":
            body = json.dumps(trading_window()).encode()
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
