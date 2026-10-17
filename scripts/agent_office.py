#!/usr/bin/env python3
"""Read-only local office viewer. Run: python3 scripts/agent_office.py"""
import argparse
import json
import sys
import time
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

_MARKET_CLOCK_CACHE = {"value": None, "fetched_at": 0.0}


def _alpaca_market_clock() -> str | None:
    """Best-effort Alpaca market clock. Cached 60s; 'unknown' if credentials
    or network are unavailable. Never blocks the office viewer."""
    now = time.time()
    if _MARKET_CLOCK_CACHE["value"] is not None and now - _MARKET_CLOCK_CACHE["fetched_at"] < 60:
        return _MARKET_CLOCK_CACHE["value"]
    try:
        from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport  # noqa: E402
        from robinhood_tools.settings import load_env  # noqa: E402
        import os

        env = {**load_env(".env"), **os.environ}
        transport = AlpacaPaperHttpTransport.from_values(env)
        backend = AlpacaPaperBackend(transport)
        clock = backend.market_clock()
        result = "open" if clock.get("is_open") else "closed"
    except Exception:  # noqa: BLE001 — viewer must stay up even if Alpaca is unreachable
        result = "unknown"
    _MARKET_CLOCK_CACHE["value"] = result
    _MARKET_CLOCK_CACHE["fetched_at"] = now
    return result


def trading_window() -> dict:
    """Whether the desk's trading window (10:15-15:45 ET weekdays) is open."""
    now = datetime.now(ET)
    start = datetime.strptime(SESSION_START, "%H:%M").time()
    end = datetime.strptime(SESSION_END, "%H:%M").time()
    open_window = now.weekday() < 5 and start <= now.time() <= end
    market = _alpaca_market_clock()
    # Trading is allowed only when BOTH the desk window is open AND the market
    # clock says the market is open (fail-closed: unknown == not tradeable).
    tradeable = open_window and market == "open"
    return {
        "window": f"{SESSION_START}-{SESSION_END} ET",
        "window_open": open_window,
        "market_clock": market,
        "tradeable": tradeable,
        "weekday": now.strftime("%A"),
        "now_et": now.isoformat(),
        "note": ("Market is open — trading allowed within the window."
                 if tradeable else
                 "Desk researches only — market clock closed or outside the window."),
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
