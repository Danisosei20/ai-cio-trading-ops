#!/usr/bin/env python3
"""Live trading-desk console (stdlib only, no build step).

Multi-page app + JSON API in one process:
  /            Overview (account, autonomy, research, guard)
  /desk        Debate timelines (bull/bear/red, judge, risk)
  /backtests   Leaderboard + price/equity charts + TradingView
  /positions   Account, positions, orders, guard
  /logs        Run log tail
  /health      Dependency health
  /api/*       JSON for every card (health, account, desk, backtests,
               charts, research, log, guard)

Read-only except /api/kill and /api/resume, which need {"confirm": true}
and only flip the emergency flag (never liquidate).

  python3 scripts/desk_server.py --port 8100
  open http://127.0.0.1:8100/

Binds loopback by default. Do NOT expose to a network without adding
authentication: kill/resume have confirmation but no auth token.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

UI_DIR = Path(__file__).resolve().parent / "desk_ui"
PAGES = {"", "index", "desk", "backtests", "positions", "logs", "health", "triggers"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
       "img-src 'self' data:; frame-src https://www.tradingview.com; connect-src 'self'; "
       "frame-ancestors 'self'; base-uri 'self'; form-action 'self'")


def load_desk(db, limit: int = 8) -> list[dict]:
    try:
        rows = db.list_trade_candidates(limit)
    except Exception:
        return []
    out = []
    for row in rows:
        try:
            out.append(db.candidate_timeline(row["candidate_id"]))
        except Exception:
            continue
    return out


def load_research(root: Path, limit: int = 30) -> list[dict]:
    rows: list[dict] = []
    if not root.exists():
        return rows
    for ticker_dir in sorted(root.iterdir()):
        if not ticker_dir.is_dir():
            continue
        for date_dir in sorted(ticker_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            auto = date_dir / "auto.json"
            if not auto.exists():
                continue
            try:
                d = json.loads(auto.read_text())
            except Exception:
                continue
            d.setdefault("ticker", ticker_dir.name)
            d.setdefault("date", date_dir.name)
            rows.append(d)
    rows.sort(key=lambda r: (r.get("date", ""), r.get("ticker", "")), reverse=True)
    return rows[:limit]


def load_lessons() -> dict:
    f = Path("outputs/paper/lessons.json")
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


TRIGGER_TICKERS = ("NVDA", "MRNA", "VRSN", "TSLA", "QQQ", "SPY")


def load_triggers() -> list[dict]:
    from robinhood_tools.alpaca_market_data import AlpacaMarketDataHttpClient
    from robinhood_tools.pullback_push import evaluate, levels_from_bars
    from robinhood_tools.settings import load_env
    import os
    from decimal import Decimal

    rows = []
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")
    try:
        env = {**load_env(".env"), **os.environ}
        client = AlpacaMarketDataHttpClient.from_values(env)
    except Exception:
        return [{"ticker": t, "signal": "UNKNOWN", "detail": "no market-data credentials"}
                for t in TRIGGER_TICKERS]
    for ticker in TRIGGER_TICKERS:
        try:
            today = datetime.now(ET).date().isoformat()
            bars = client.stock_bars(ticker, timeframe="1D", start="2026-01-01",
                                     end=today, limit=100, sort="desc")
            bars = list(reversed(bars))
            closes = [float(b["c"]) for b in bars]
            highs = [float(b["h"]) for b in bars]
            lows = [float(b["l"]) for b in bars]
            levels = levels_from_bars(closes, highs, lows)
            state = evaluate(Decimal(str(closes[-1])), levels)
            rows.append({"ticker": ticker, "price": round(closes[-1], 2),
                         "support": str(levels.support.quantize(Decimal("0.01"))),
                         "resistance": str(levels.resistance.quantize(Decimal("0.01"))),
                         "atr": str(levels.atr.quantize(Decimal("0.01"))),
                         "signal": state.signal, "detail": state.detail})
        except Exception as exc:  # noqa: BLE001 - one bad ticker never blocks the page
            rows.append({"ticker": ticker, "signal": "UNKNOWN",
                         "detail": f"{type(exc).__name__}"})
    return rows


def load_backtest() -> dict:
    f = Path("outputs/backtests/latest.json")
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def load_log(lines: int = 80) -> str:
    f = Path("outputs/paper/tradingagents/live.log")
    if not f.exists():
        return "(no live log yet)"
    try:
        with open(f, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            chunk = fh.read().decode("utf-8", errors="replace")
        return "\n".join(chunk.splitlines()[-lines:])
    except Exception:
        return "(log unreadable)"


def load_guard() -> dict:
    f = Path("outputs/paper/guard.json")
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


_ACCOUNT_CACHE: dict = {"at": 0.0, "payload": None}


def account_snapshot(settings, ttl_seconds: float = 10.0) -> dict:
    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
    from robinhood_tools.settings import load_env
    import os
    import time

    now = time.time()
    if _ACCOUNT_CACHE["payload"] is not None and now - _ACCOUNT_CACHE["at"] < ttl_seconds:
        return _ACCOUNT_CACHE["payload"]
    last_error = "unknown"
    for attempt in (1, 2):
        try:
            env = {**load_env(".env"), **os.environ}
            backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
            acct = backend.transport.request("GET", "/v2/account")
            equity_curve = []
            try:
                hist = backend.transport.request(
                    "GET", "/v2/account/portfolio/history?period=1D&timeframe=5Min")
                eq = hist.get("equity") or []
                ts = hist.get("timestamp") or []
                base = hist.get("base_value") or (eq[0] if eq else 0)
                equity_curve = [{"t": t, "v": round(float(v) / float(base or 1), 5)}
                                for t, v in zip(ts, eq) if v]
            except Exception:
                equity_curve = []
            payload = {"ok": True,
                       "account": backend.list_accounts()[0].masked_account_number,
                       "cash": str(acct.get("cash")), "buying_power": str(acct.get("buying_power")),
                       "portfolio": str(acct.get("portfolio_value")),
                       "clock": backend.market_clock().get("is_open"),
                       "positions": backend.list_positions(),
                       "orders": backend.list_open_orders()[:25],
                       "equity_curve": equity_curve}
            _ACCOUNT_CACHE.update(at=time.time(), payload=payload)
            return payload
        except Exception as exc:  # noqa: BLE001 - UI degrades, never crashes
            last_error = f"{type(exc).__name__}"
            print(f"desk account_snapshot attempt {attempt} failed: {last_error}", flush=True)
            time.sleep(1)
    return {"ok": False, "error": last_error}


class Handler(BaseHTTPRequestHandler):
    database_path: str = "outputs/paper/cio.db"
    server_version = "DeskUI/2"

    def _send(self, code: int, obj=None, content_type="application/json",
              cache: str = "no-store") -> None:
        body = obj if isinstance(obj, bytes) else json.dumps(obj or {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: Path, content_type: str, cache: str = "no-store") -> None:
        try:
            if not path.is_file():
                self._send(404, {"ok": False, "error": "not found"})
                return
            self._send(200, path.read_bytes(), content_type, cache=cache)
        except Exception:
            self._send(500, {"ok": False, "error": "read failed"})

    def do_GET(self) -> None:  # noqa: N802
        from robinhood_tools.database import CioDatabase
        from robinhood_tools.runtime import build_settings

        parsed = urllib.parse.urlparse(self.path)
        name = parsed.path.strip("/").split("/")[0]
        if parsed.path.startswith("/static/"):
            rel = urllib.parse.unquote(parsed.path[len("/static/"):])
            target = (UI_DIR / rel).resolve()
            if UI_DIR.resolve() not in target.parents and target != UI_DIR.resolve():
                self._send(404, {"ok": False, "error": "not found"})
                return
            ctype, _ = mimetypes.guess_type(target.name)
            self._serve_file(target, ctype or "application/octet-stream",
                             cache="public, max-age=3600")
            return
        if name in PAGES:
            page = "index.html" if name in {"", "index"} else f"{name}.html"
            self._serve_file(UI_DIR / page, "text/html; charset=utf-8")
            return
        try:
            settings = build_settings()
            db = CioDatabase(self.database_path)
            if parsed.path == "/api/health":
                try:
                    db.require_not_killed()
                    killed = False
                except Exception:
                    killed = True
                try:
                    clock = account_snapshot(settings).get("clock")
                except Exception:
                    clock = None
                self._send(200, {
                    "mode": settings.mode, "live_enabled": settings.trading_enabled,
                    "paper_auto": settings.paper_autonomy.enabled and settings.paper_trading_enabled,
                    "killed": killed, "exec_policy": "OBSERVE",
                    "window": f"{settings.paper_autonomy.earliest_entry_time_et}-"
                              f"{settings.paper_autonomy.latest_entry_time_et} ET",
                    "max_order": str(settings.risk_limits.max_order_value),
                    "market_open": clock,
                    "now_et": datetime.now().astimezone().strftime("%H:%M %Z")})
            elif parsed.path == "/api/account":
                self._send(200, account_snapshot(settings))
            elif parsed.path == "/api/desk":
                self._send(200, load_desk(db))
            elif parsed.path in ("/api/backtests", "/api/charts"):
                self._send(200, load_backtest())
            elif parsed.path == "/api/research":
                self._send(200, load_research(Path("outputs/paper/tradingagents")))
            elif parsed.path == "/api/log":
                self._send(200, {"tail": load_log()})
            elif parsed.path == "/api/guard":
                self._send(200, load_guard())
            elif parsed.path == "/api/triggers":
                self._send(200, load_triggers())
            elif parsed.path == "/api/lessons":
                self._send(200, load_lessons())
            else:
                self._send(404, {"ok": False, "error": "unknown endpoint"})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"ok": False, "error": type(exc).__name__})

    def do_POST(self) -> None:  # noqa: N802
        from robinhood_tools.database import CioDatabase

        path = urllib.parse.urlparse(self.path).path
        if path not in ("/api/kill", "/api/resume"):
            self._send(404, {"ok": False, "error": "unknown endpoint"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send(400, {"ok": False, "error": "bad JSON"})
            return
        if body.get("confirm") is not True:
            self._send(422, {"ok": False, "error": "confirmation required"})
            return
        try:
            db = CioDatabase(self.database_path)
            if path == "/api/kill":
                db.set_emergency_kill(True)
                db.audit("ui_kill_switch", {"by": "operator-ui"}, correlation_id="ui",
                         approval_id=None)
            else:
                db.set_emergency_kill(False)
                db.audit("ui_resume", {"by": "operator-ui"}, correlation_id="ui",
                         approval_id=None)
            self._send(200, {"ok": True})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"ok": False, "error": type(exc).__name__})

    def log_message(self, *args) -> None:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Live trading-desk console.")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--database", default="outputs/paper/cio.db")
    a = ap.parse_args()
    Handler.database_path = a.database
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Desk console on http://{a.host}:{a.port}/  (db {a.database})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
