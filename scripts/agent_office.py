#!/usr/bin/env python3
"""Read-only local office viewer. Run: python3 scripts/agent_office.py"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import desk_safety_status  # noqa: E402
from scripts.market_state import market_payload, universe_symbols  # noqa: E402
from scripts.options_candidates import option_candidates  # noqa: E402
from scripts.office_state import snapshot  # noqa: E402
from scripts.short_basket import short_basket  # noqa: E402

ET = ZoneInfo("America/New_York")
SESSION_START = "10:15"
SESSION_END = "15:45"
# Symbols are used in local artifact glob patterns, so reject path/glob syntax.
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_MARKET_CLOCK_CACHE = {"value": None, "fetched_at": 0.0}

# Safety posture is read-only and changes slowly. Cache the whole payload for
# 30s so the 2s status poll never re-runs schtasks or the broker read.
_SAFETY = {"payload": None, "at": 0.0}
_SAFETY_TTL = 30.0


def _safety_payload() -> dict:
    now = time.monotonic()
    if _SAFETY["payload"] is None or now - _SAFETY["at"] >= _SAFETY_TTL:
        try:
            payload = desk_safety_status.snapshot(ROOT)
        except Exception:  # noqa: BLE001 - a broken reader fails closed
            payload = {
                "entries": "UNKNOWN", "mode": "UNKNOWN", "account": "UNKNOWN",
                "certification": {"status": "NOT VERIFIED", "generated_at": "",
                                  "label": "NOT VERIFIED"},
                "reconciliation": {"status": "never run", "orphans": 0},
                "protective": {"status": "UNKNOWN", "tasks": []},
                "alerts": "NOT VERIFIED",
                "generated_at": datetime.now(ET).isoformat(),
            }
        _SAFETY["payload"] = payload
        _SAFETY["at"] = now
    return _SAFETY["payload"]


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


def execution_safety_payload(root: Path) -> dict:
    """Read-only execution authorization telemetry. Never places orders."""
    from robinhood_tools import entry_risk, execution_authorization as ea

    def key_telemetry() -> dict:
        try:
            from robinhood_tools import authorization_keys as kmod
            store = kmod.KeyStore()
            rows = store.status()
            active = [r for r in rows if r["status"] == "ACTIVE"]
            verify_only = [r for r in rows if r["status"] == "VERIFY_ONLY"]
            return {"health": "ok" if store.verify_store() else "integrity_failure",
                    "active_kid": active[0]["kid"] if active else None,
                    "verification_key_count": len(verify_only),
                    "last_rotation": active[0]["activated_at"] if active else None}
        except Exception as exc:  # noqa: BLE001 - viewer never fails on telemetry
            return {"health": "unavailable", "reason": type(exc).__name__}

    def revocation_count() -> int:
        import sqlite3
        try:
            conn = sqlite3.connect(str(root / "outputs/paper/execution_authorizations.sqlite"))
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS revocations ("
                             "auth_id TEXT PRIMARY KEY, reason TEXT, revoked_at TEXT)")
                return int(conn.execute("SELECT COUNT(*) FROM revocations").fetchone()[0])
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            return 0

    def failed_validations() -> int:
        try:
            from robinhood_tools.authorization_audit import DEFAULT_AUDIT
            path = root / DEFAULT_AUDIT
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            return text.count('"authorization_rejected"') + text.count('"signature verification failure"')
        except OSError:
            return 0

    def read_json(path: Path) -> dict:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            return doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            return {}

    latest = read_json(root / "outputs/orchestrator/latest.json")
    index = read_json(root / "outputs/paper/authorization_index.json")
    run_id = str(latest.get("run_id", ""))
    auth = {}
    if run_id and run_id in index:
        try:
            from robinhood_tools.execution_authorization import load_authorization
            auth = load_authorization(index[run_id], root) or {}
        except Exception:  # noqa: BLE001 - viewer never fails on a bad artifact
            auth = {}
    return {
        "read_only": True,
        "enforcement_enabled": ea.enforcement_enabled(),
        "kill_switch": entry_risk.kill_switch_engaged(
            root, db_kill=(root / "outputs/paper/KILL_SWITCH").exists()),
        "advisor_state": "ADR 0002 approved (2026-11-23); enforcement OFF pending rehearsal",
        "gate_verdict": latest.get("verdict", "unavailable"),
        "gate_run_id": run_id or None,
        "authorization": {
            "id": auth.get("authorization_id"),
            "state": auth.get("state"),
            "expires_at": auth.get("expires_at"),
            "decision_id": auth.get("decision_id"),
            "account_id": auth.get("account_id"),
        } if auth else None,
        "authorization_count": len(index),
        "authorization_service_health": key_telemetry()["health"],
        "signing_key": {"active_kid": key_telemetry().get("active_kid")},
        "verification_key_count": key_telemetry().get("verification_key_count", 0),
        "last_rotation": key_telemetry().get("last_rotation"),
        "revoked_authorization_count": revocation_count(),
        "failed_validations": failed_validations(),
        "limitations": "Telemetry only. No live orders, no approval actions from this viewer.",
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route == "/api/status":
            body = json.dumps({**snapshot(ROOT), "session": trading_window(),
                               "safety": _safety_payload()}).encode()
            mime = "application/json"
        elif route == "/api/safety":
            body = json.dumps(_safety_payload()).encode()
            mime = "application/json"
        elif route == "/api/execution-safety":
            try:
                payload = execution_safety_payload(ROOT)
            except Exception as exc:  # noqa: BLE001 - fail closed, viewer keeps working
                payload = {"read_only": True, "status": "unavailable",
                           "reason": f"{type(exc).__name__}: {exc}"}
            body = json.dumps(payload).encode()
            mime = "application/json"
        elif route == "/api/session":
            body = json.dumps(trading_window()).encode()
            mime = "application/json"
        elif route == "/api/symbols":
            body = json.dumps(universe_symbols(ROOT, now_iso=datetime.now(ET).isoformat())).encode()
            mime = "application/json"
        elif route == "/api/options":
            # The read-only "/options" page calls this data route; no write route.
            try:
                payload = option_candidates(ROOT)
            except Exception:  # noqa: BLE001 — a broken reader fails closed
                payload = {"status": "unavailable", "reason": "option candidates unavailable"}
            body = json.dumps(payload).encode()
            mime = "application/json"
        elif route == "/api/basket":
            # Signal-only proposal; a broken reader fails closed. No write route.
            try:
                payload = short_basket(ROOT)
            except Exception:  # noqa: BLE001 — a broken reader fails closed
                payload = {"status": "unavailable", "reason": "basket signal unavailable"}
            body = json.dumps(payload).encode()
            mime = "application/json"
        elif route == "/api/market":
            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            symbol = query.get("symbol", ["NVDA"])[0].strip().upper()
            if not _SYMBOL_RE.match(symbol):
                payload = {"status": "unavailable", "reason": "invalid symbol"}
            else:
                try:
                    payload = market_payload(ROOT, symbol, now_iso=datetime.now(ET).isoformat())
                except Exception:  # noqa: BLE001 — a broken reader fails closed
                    payload = {"status": "unavailable", "reason": "market snapshot unavailable"}
            body = json.dumps(payload).encode()
            mime = "application/json"
        elif route == "/api/trading":
            try:
                from scripts.trading_state import state as trading_state  # noqa: E402

                payload = json.dumps(trading_state(), default=str).encode()
            except Exception as exc:  # noqa: BLE001 — the page renders an error card
                payload = json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode()
            body = payload
            mime = "application/json"
        elif route == "/api/news":
            # Read-only: newest explanation artifact, or a fail-closed doc.
            # Task 2 writes these artifacts; this route never writes anything.
            try:
                pattern = ROOT / "outputs/news/explanations/*.json"
                matches = sorted(pattern.parent.glob(pattern.name),
                                 key=lambda p: (p.stat().st_mtime, str(p)))
                if matches:
                    payload = json.loads(matches[-1].read_text(encoding="utf-8"))
                else:
                    payload = {"status": "unavailable",
                               "reason": "no news explanation artifact"}
            except Exception:  # noqa: BLE001 - a broken artifact fails closed
                payload = {"status": "unavailable",
                           "reason": "no news explanation artifact"}
            body = json.dumps(payload).encode()
            mime = "application/json"
        elif route == "/trading":
            body = (ROOT / "web/agent-office" / "trading.html").read_bytes()
            mime = "text/html"
        elif route == "/pipeline":
            body = (ROOT / "web/agent-office" / "pipeline.html").read_bytes()
            mime = "text/html"
        elif route == "/basket":
            # The page asset is delivered by the basket-view task; fail closed
            # with a 404 rather than crashing the viewer if it is not present.
            page = ROOT / "web/agent-office" / "basket.html"
            if not page.is_file():
                self.send_error(404)
                return
            body = page.read_bytes()
            mime = "text/html"
        elif route == "/news":
            # Read-only page for source-cited news explanations. No write route.
            page = ROOT / "web/agent-office" / "news.html"
            if not page.is_file():
                self.send_error(404)
                return
            body = page.read_bytes()
            mime = "text/html"
        elif route == "/market":
            body = (ROOT / "web/agent-office" / "market.html").read_bytes()
            mime = "text/html"
        elif route == "/options":
            body = (ROOT / "web/agent-office" / "options.html").read_bytes()
            mime = "text/html"
        elif route == "/shell":
            body = (ROOT / "web/agent-office" / "shell.html").read_bytes()
            mime = "text/html"
        elif route in ("/", "/office.js", "/office.css", "/scene.js", "/market.js", "/options.js", "/basket.js", "/news.js", "/shell", "/shell.js", "/shell_safety.js"):
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
