"""Alert webhook server: the TradingKit-equivalent for this stack.

Receives strategy alerts (JSON POST) and routes them to Alpaca paper only.
No TradingView subscription needed: TradingAgents, cron, or any signal
source can POST directly.

  POST /alert  {"symbol":"NVDA","side":"buy","qty":2,"limit":227.80,
                "earnings_date":"2026-11-20",
                "secret":"<ALERT_WEBHOOK_SECRET>"}
  GET  /health -> {"ok":true,"mode":"paper_auto"}
  GET  /recent -> last 25 alert outcomes (in-memory + mirrored to live.log)

Gates (paper-only, fail closed):
- HMAC-style shared secret (ALERT_WEBHOOK_SECRET env, required)
- mode=paper_auto, PAPER_TRADING_ENABLED=true, TRADING_ENABLED=false
- S&P500 membership (<24h Wikipedia snapshot, cached 1h)
- earnings blackout for buys: alerts must carry "earnings_date" (YYYY-MM-DD);
  missing/malformed dates reject the buy (fail closed; this server is
  stdlib-only by design and performs no earnings lookup itself)
- $500 order/symbol cap, buying-power check, DAY limit only
- Alpaca review fingerprint + CioDatabase approval ledger + atomic place
- Session guard via the paper service (10:15-15:30 ET + clock open);
  outside the window the alert is recorded as rejected_window, never queued

Run (TradingAgents venv not required, stdlib only):
  ALERT_WEBHOOK_SECRET='...' python3 scripts/alert_webhook.py --port 8090
Test:
  curl -s localhost:8090/health
  curl -s -X POST localhost:8090/alert -H 'Content-Type: application/json' \\
    -d '{"symbol":"NVDA","side":"buy","qty":1,"limit":230.00,"earnings_date":"2026-11-20","secret":"..."}'
"""
from __future__ import annotations

import argparse
import hmac
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ET = ZoneInfo("America/New_York")
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
LIVE_LOG = Path("outputs/paper/tradingagents/live.log")
RECENT: list[dict] = []
_SP500_CACHE: dict = {}


def log_line(message: str) -> None:
    line = f"{datetime.now(ET).isoformat()} [webhook] {message}"
    print(line, flush=True)
    try:
        LIVE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LIVE_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def fetch_sp500() -> tuple[frozenset, str]:
    from robinhood_tools.errors import PolicyViolation

    now = datetime.now(timezone.utc).timestamp()
    if _SP500_CACHE and now - _SP500_CACHE.get("ts", 0) < 3600:
        return _SP500_CACHE["symbols"], _SP500_CACHE["as_of"]
    req = urllib.request.Request(SP500_URL, headers={"User-Agent": "paper-webhook/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        page = resp.read().decode("utf-8", "replace")
    m = re.search(r"List of S&amp;P 500 companies.*?(<table.*?</table>)", page, re.S)
    table = m.group(1) if m else page
    symbols = frozenset(s.upper().replace(".", "-") for s in re.findall(r">([A-Z]{1,5}(?:\.[A-Z])?)<", table))
    if len(symbols) < 400:
        raise PolicyViolation(f"S&P500 fetch returned {len(symbols)} symbols; failing closed.")
    as_of = datetime.now(timezone.utc).isoformat()
    _SP500_CACHE.update(symbols=symbols, as_of=as_of, ts=now)
    return symbols, as_of


def handle_alert(payload: dict, settings, env: dict) -> dict:
    import os

    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
    from robinhood_tools.errors import PolicyViolation
    from robinhood_tools.paper_flow import SignalOrder, place_signal_order
    from robinhood_tools.universe import Sp500Snapshot

    secret = str(payload.get("secret", ""))
    expected = os.environ.get("ALERT_WEBHOOK_SECRET", "")
    if not expected or not hmac.compare_digest(secret, expected):
        raise PolicyViolation("invalid webhook secret")
    if settings.trading_enabled:
        raise PolicyViolation("TRADING_ENABLED=true; paper webhook refuses to run")
    settings.require_paper_trading()
    if not settings.paper_trading_enabled:
        raise PolicyViolation("PAPER_TRADING_ENABLED=false")

    symbol = str(payload.get("symbol", "")).upper().strip()
    side = str(payload.get("side", "")).lower().strip()
    if not re.fullmatch(r"[A-Z]{1,5}", symbol) or side not in {"buy", "sell"}:
        raise PolicyViolation("symbol must be 1-5 letters and side buy/sell")
    try:
        qty = int(payload.get("qty", 0))
        limit = Decimal(str(payload.get("limit", "0")))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PolicyViolation(f"bad qty/limit: {exc}") from exc
    if qty < 1 or limit <= 0:
        raise PolicyViolation("qty>=1 and limit>0 required")

    from datetime import date as _date

    earnings_date = None
    if side == "buy":
        try:
            earnings_date = _date.fromisoformat(
                str(payload.get("earnings_date", "") or "").strip())
        except ValueError as exc:
            raise PolicyViolation(
                "buys require earnings_date YYYY-MM-DD in the alert") from exc

    symbols, as_of = fetch_sp500()
    snapshot = Sp500Snapshot(symbols=symbols, as_of=as_of, source_url=SP500_URL)
    snapshot.require_current_member(symbol)

    cost = limit * qty
    cap = min(Decimal("500"), settings.risk_limits.max_order_value)
    if cost > cap:
        raise PolicyViolation(f"cost ~${cost} exceeds ${cap} paper cap")

    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    acct = backend.transport.request("GET", "/v2/account")
    if side == "buy" and Decimal(str(acct.get("buying_power", "0"))) < cost:
        raise PolicyViolation("insufficient paper buying power")
    if side == "sell":
        held = sum(Decimal(str(p.get("qty", "0"))) for p in backend.list_positions()
                   if str(p.get("symbol", "")).upper() == symbol)
        if held < qty:
            raise PolicyViolation(f"holds {held} {symbol}, cannot sell {qty}")

    result = place_signal_order(
        settings=settings, snapshot=snapshot,
        order=SignalOrder(symbol=symbol, side=side,  # type: ignore[arg-type]
                          quantity=Decimal(qty),
                          limit_price=limit.quantize(Decimal("0.01")),
                          earnings_date=earnings_date,
                          max_order_value=cap),
        env=env)
    return {"action": result["action"], "symbol": symbol, "qty": qty,
            "limit": str(limit.quantize(Decimal("0.01"))),
            "order_id": result["order_id"], "status": result["status"],
            "approval_id": result["approval_id"]}


class Handler(BaseHTTPRequestHandler):
    settings = None
    env: dict = {}

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            mode = self.settings.mode if self.settings else "unknown"
            self._send(200, {"ok": True, "mode": mode, "broker": "alpaca-paper"})
        elif self.path == "/recent":
            self._send(200, {"recent": RECENT[-25:]})
        else:
            self._send(404, {"ok": False, "error": "use POST /alert, GET /health, GET /recent"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/alert":
            self._send(404, {"ok": False, "error": "use POST /alert"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            outcome = handle_alert(payload, self.settings, self.env)
            outcome["ok"] = True
            RECENT.append({**outcome, "at": datetime.now(ET).isoformat()})
            log_line(f"ALERT {outcome['action']} {outcome['symbol']} x{outcome['qty']} order={outcome['order_id']}")
            self._send(200, outcome)
        except Exception as exc:  # noqa: BLE001 - webhook must always answer
            reason = f"{type(exc).__name__}: {str(exc)[:200]}"
            RECENT.append({"ok": False, "at": datetime.now(ET).isoformat(), "error": reason})
            log_line(f"ALERT rejected: {reason}")
            self._send(422, {"ok": False, "error": reason})

    def log_message(self, *args) -> None:  # keep stdout clean; use live.log
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Paper-only alert webhook (TradingKit equivalent).")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address; use 0.0.0.0 only inside containers")
    ap.add_argument("--config", default="config/approval_routes.json")
    ap.add_argument("--env-file", default=".env")
    a = ap.parse_args()

    import os

    from robinhood_tools.runtime import build_settings
    from robinhood_tools.settings import load_env

    if not os.environ.get("ALERT_WEBHOOK_SECRET"):
        print("REFUSAL: set ALERT_WEBHOOK_SECRET env first", file=sys.stderr)
        return 2
    Handler.settings = build_settings(a.config, a.env_file)
    Handler.env = {**load_env(a.env_file), **os.environ}
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    log_line(f"listening on 127.0.0.1:{a.port} mode={Handler.settings.mode}")
    print(f"Alert webhook on http://127.0.0.1:{a.port} (paper only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
