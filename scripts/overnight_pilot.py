#!/usr/bin/env python3
"""Paper overnight-gap pilot: enter near the close, exit at the open.

Mirrors the backtested overnight50 rule (buy at close when close>50d SMA,
sell next open) with real gated paper orders. Small fixed notional ($250)
until live paper confirms or kills the edge.

Sessions (strategy-specific; global 10:15-15:30 window untouched):
  enter: weekdays 15:30-15:55 ET + market clock open
  exit:  weekdays 09:35-10:00 ET + market clock open

  ../TradingAgents/.venv/bin/python scripts/overnight_pilot.py --enter --dry-run
  ../TradingAgents/.venv/bin/python scripts/overnight_pilot.py --enter
  ../TradingAgents/.venv/bin/python scripts/overnight_pilot.py --exit

State: outputs/paper/overnight.json (entries). Exits sell recorded entries
that are still held. Every placement goes through place_signal_order
(review + ledger + session guard). Paper only.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.errors import PolicyViolation  # noqa: E402
from robinhood_tools.paper_flow import SignalOrder, place_signal_order  # noqa: E402
from robinhood_tools.runtime import (  # noqa: E402
    build_paper_service_with_session, build_settings)

ENTER_WINDOW = ("15:30", "15:55")
EXIT_WINDOW = ("09:35", "10:00")
STATE_FILE = Path("outputs/paper/overnight.json")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Paper overnight-gap pilot.")
    p.add_argument("--enter", action="store_true", help="afternoon entry run")
    p.add_argument("--exit", action="store_true", help="morning exit run")
    p.add_argument("--tickers", default="NVDA")
    p.add_argument("--notional", type=Decimal, default=Decimal("250"))
    p.add_argument("--trend", type=int, default=50)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def uptrend_ok(closes: list[float], trend: int) -> bool:
    """Close above N-day SMA using only settled bars. Pure, tested."""
    if len(closes) < trend + 1:
        return False
    return closes[-1] > sum(closes[-trend - 1:-1]) / trend


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {"entries": []}
    return {"entries": []}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main(argv=None) -> int:
    args = parse_args(argv)
    if bool(args.enter) == bool(args.exit):
        print("Choose exactly one of --enter / --exit.", file=sys.stderr)
        return 2
    settings = build_settings(args.config, args.env_file)
    if settings.trading_enabled:
        print("REFUSAL: live kill switch open.", file=sys.stderr)
        return 2

    import os

    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
    from robinhood_tools.database import CioDatabase
    from robinhood_tools.settings import load_env
    from robinhood_tools.universe import Sp500Snapshot
    import urllib.request
    import re

    SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

    env = {**load_env(args.env_file), **os.environ}
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    db = CioDatabase(settings.database_path)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    if args.enter:
        req = urllib.request.Request(SP500_URL, headers={"User-Agent": "desk-overnight/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            page = resp.read().decode("utf-8", "replace")
        m = re.search(r"List of S&amp;P 500 companies.*?(<table.*?</table>)", page, re.S)
        table = m.group(1) if m else page
        members = {s.upper().replace(".", "-") for s in re.findall(r">([A-Z]{1,5}(?:\.[A-Z])?)<", table)}
        from datetime import datetime, timezone
        snapshot = Sp500Snapshot(symbols=frozenset(members),
                                 as_of=datetime.now(timezone.utc).isoformat(),
                                 source_url=SP500_URL)
        service = build_paper_service_with_session(
            settings=settings, sp500_snapshot=snapshot, env_path=args.env_file,
            earliest_entry_et=ENTER_WINDOW[0], latest_entry_et=ENTER_WINDOW[1])
        import yfinance as yf  # noqa: PLC0415 (TradingAgents venv)

        state = load_state()
        for ticker in tickers:
            rec = {"ticker": ticker, "mode": "enter", "action": "skip", "reason": ""}
            try:
                snapshot.require_current_member(ticker)
                hist = yf.Ticker(ticker).history(period="1y", interval="1d")
                closes = [float(v) for v in hist["Close"].dropna()]
                if not uptrend_ok(closes, args.trend):
                    rec["reason"] = f"close below {args.trend}d SMA; no entry"
                    print(json.dumps(rec))
                    continue
                price = Decimal(str(closes[-1]))
                try:
                    earnings = __import__("robinhood_tools.paper_flow",
                                          fromlist=["lookup_earnings_date"]).lookup_earnings_date(ticker)
                except Exception:
                    earnings = None
                qty = int(args.notional // price) if price > 0 else 0
                if qty < 1:
                    rec["reason"] = f"price {price} exceeds ${args.notional} pilot notional"
                    print(json.dumps(rec))
                    continue
                candidate_id = f"{ticker}:{date.today().isoformat()}:overnight:{uuid.uuid4().hex[:8]}"
                db.record_candidate(candidate_id, ticker, "buy",
                                    {"source": "overnight-rule", "trend": args.trend,
                                     "close": str(price)})
                db.record_opinion(candidate_id, "market", "bullish", 60,
                                  {"evidence": [f"close>50d SMA @ {price}"]})
                if args.dry_run:
                    rec.update({"action": "dry_run_buy", "qty": qty,
                                "limit": str(price.quantize(Decimal("0.01")))})
                    print(json.dumps(rec))
                    continue
                result = place_signal_order(
                    settings=settings, snapshot=snapshot,
                    order=SignalOrder(symbol=ticker, side="buy",
                                      quantity=Decimal(qty),
                                      limit_price=price.quantize(Decimal("0.01")),
                                      earnings_date=earnings,
                                      max_order_value=args.notional),
                    database=db, service=service)
                db.update_candidate_status(candidate_id, "approved")
                state["entries"].append({"symbol": ticker, "qty": qty,
                                        "date": date.today().isoformat(),
                                        "entry_price": str(price.quantize(Decimal("0.01"))),
                                        "order_id": result["order_id"]})
                save_state(state)
                rec.update({"action": "buy_placed", "qty": qty,
                            "order_id": result["order_id"], "status": result["status"]})
                print(json.dumps(rec))
            except (PolicyViolation, InvalidOperation, KeyError) as exc:
                print(json.dumps({"ticker": ticker, "mode": "enter",
                                  "action": "skip", "reason": f"{type(exc).__name__}: {exc}"}))
        return 0

    # --exit
    service = build_paper_service_with_session(
        settings=settings,
        sp500_snapshot=Sp500Snapshot(symbols=frozenset(),
                                     as_of="2000-01-01T00:00:00+00:00",
                                     source_url="https://example.com/unused-for-sells"),
        env_path=args.env_file,
        earliest_entry_et=EXIT_WINDOW[0], latest_entry_et=EXIT_WINDOW[1])
    state = load_state()
    remaining = []
    for entry in state.get("entries", []):
        symbol = entry["symbol"]
        try:
            held = sum(Decimal(str(p.get("qty", "0"))) for p in backend.list_positions()
                       if str(p.get("symbol", "")).upper() == symbol)
            if held <= 0:
                continue  # already flat; drop from state
            import yfinance as yf  # noqa: PLC0415

            day = yf.Ticker(symbol).history(period="1d", interval="1m")
            mark = Decimal(str(day["Close"].iloc[-1])) if len(day) else None
            if mark is None:
                day = yf.Ticker(symbol).history(period="5d", interval="1d")
                mark = Decimal(str(day["Close"].iloc[-1]))
            if args.dry_run:
                print(json.dumps({"ticker": symbol, "mode": "exit",
                                  "action": "dry_run_sell", "qty": str(held),
                                  "mark": str(mark)}))
                remaining.append(entry)
                continue
            result = place_signal_order(
                settings=settings, snapshot=None,
                order=SignalOrder(symbol=symbol, side="sell", quantity=held,
                                  limit_price=mark.quantize(Decimal("0.01"))),
                database=db, service=service)
            print(json.dumps({"ticker": symbol, "mode": "exit", "action": "sell_placed",
                              "qty": str(held), "order_id": result["order_id"],
                              "status": result["status"]}))
        except (PolicyViolation, InvalidOperation, KeyError) as exc:
            remaining.append(entry)
            print(json.dumps({"ticker": symbol, "mode": "exit", "action": "skip",
                              "reason": f"{type(exc).__name__}: {exc}"}))
    state["entries"] = remaining
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
