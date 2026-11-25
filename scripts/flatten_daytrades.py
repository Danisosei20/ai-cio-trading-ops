#!/usr/bin/env python3
"""Flatten day-trade positions before the close (owner: flat by 15:45 ET).

A day trade that survives the close is not a day trade - it carries
overnight risk the desk never asked for. This reads today's auto.json
records tagged trade_style=day_trade, checks the broker, and sells any
still-held position at the current quote before the session window ends.

Options are NOT flattened here: their exits belong to options_guard
(-25% stop / +target / DTE) and their own window rules.

Usage:
  python scripts/flatten_daytrades.py --check   # report only
  python scripts/flatten_daytrades.py           # sell what is still held
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
AUTO_DIR = ROOT / "outputs" / "paper" / "tradingagents"
from robinhood_tools.settings import load_env  # noqa: E402


def _today_records(today: str) -> list[dict]:
    records = []
    if AUTO_DIR.exists():
        for path in sorted(AUTO_DIR.glob(f"*/{today}/auto.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if rec.get("trade_style") == "day_trade":
                records.append(rec)
    return records


def owned_quantity(rec: dict) -> Decimal | None:
    """Explicit quantity recorded for this day trade; None when ambiguous."""
    for key in ("quantity", "qty", "position_qty"):
        raw = rec.get(key)
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except Exception:  # noqa: BLE001 - malformed quantity is ambiguous
            return None
        return value if value > 0 else None
    return None


def sellable_quantity(rec: dict, held: Decimal) -> Decimal:
    """Never close more than the trade's own recorded broker quantity."""
    owned = owned_quantity(rec)
    if owned is None or held == 0:
        return Decimal("0")
    return min(owned, abs(held))


def close_plan(rec: dict, held: Decimal,
               working: set[tuple[str, str]]) -> tuple[Decimal, str, str]:
    """Return (quantity, side, reason). A non-empty reason is a refusal."""
    symbol = str(rec.get("ticker", "")).upper()
    if held == 0:
        return Decimal("0"), "", "already flat"
    side = "sell" if held > 0 else "buy"
    if (symbol, side) in working:
        return Decimal("0"), "", "a close order is already working"
    owned = owned_quantity(rec)
    if owned is None:
        return Decimal("0"), "", "no owned quantity recorded"
    qty = sellable_quantity(rec, held)
    if qty <= 0:
        return Decimal("0"), "", "no owned quantity at broker"
    return qty, side, ""

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Flatten day-trade positions before the close.")
    p.add_argument("--check", action="store_true", help="report only; sell nothing")
    p.add_argument("--date", default=None, help="trade date (default today ET)")
    args = p.parse_args(argv)

    today = args.date or datetime.now(ET).date().isoformat()
    records = [r for r in _today_records(today)
               if str(r.get("action", "")).endswith("_placed")]
    failures: list[str] = []
    if not records:
        print(f"flatten_daytrades: no day-trade placements for {today}")
        return 0

    from robinhood_tools.alpaca_paper import (AlpacaPaperBackend,
                                              AlpacaPaperHttpTransport)
    from robinhood_tools.paper_flow import SignalOrder, place_signal_order
    from robinhood_tools.runtime import build_settings

    settings = build_settings("config/approval_routes.json", ".env")
    env = {**load_env(".env"), **os.environ}
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    positions = {str(p.get("symbol", "")).upper(): Decimal(str(p.get("qty", "0")))
                 for p in backend.list_positions()}
    # Alpaca orders do not carry position_effect; a working sell against a
    # long or a working buy against a short is a potential duplicate close.
    working = {(str(o.get("symbol", "")).upper(),
                str(o.get("side", "")).lower())
               for o in backend.list_open_orders()
               if str(o.get("side", "")).lower() in {"sell", "buy"}}

    for rec in records:
        symbol = str(rec.get("ticker", "")).upper()
        held = positions.get(symbol, Decimal("0"))
        qty, side, reason = close_plan(rec, held, working)
        if reason:
            if reason == "already flat":
                print(f"flatten_daytrades: {symbol} already flat")
            else:
                failures.append(f"{symbol}: {reason}")
                print(f"flatten_daytrades: {symbol} close refused: {reason}")
            continue
        if qty < abs(held):
            print(f"flatten_daytrades: {symbol} broker holds {held}, this trade owns {qty}; "
                  f"leaving {abs(held) - qty} for its owning strategy")
        if args.check:
            print(f"flatten_daytrades: {symbol} still held {held} -> would {side} to close")
            continue
        try:
            from robinhood_tools.market_prices import get_quote

            price = get_quote(symbol).price
            result = place_signal_order(
                settings=settings, snapshot=None,
                order=SignalOrder(symbol=symbol, side=side,
                    position_effect="close",
                                  quantity=qty,
                                  limit_price=price.quantize(Decimal("0.01"))),
                env_path=".env")
            print(f"flatten_daytrades: {symbol} {side} {qty} @ {price} -> "
                  f"{result['status']} (order {result['order_id']})")
            if str(result.get("status", "")).lower() in {"rejected", "failed", "error"}:
                failures.append(f"{symbol}: broker rejected close")
            else:
                # Refresh the local broker view so a second record for the same
                # symbol cannot sell the same exposure twice and flip it short.
                positions[symbol] = held - qty
                working.add((symbol, side))
            # The Slack alert is best-effort; its outcome is now recorded by
            # place_signal_order. A flatten the operator cannot see is a
            # flatten they cannot audit, so say so plainly when it happens.
            if result.get("alert") == "failed":
                print(f"flatten_daytrades: {symbol} ALERT FAILED - the sell "
                      f"is in the ledger and at the broker, but no Slack "
                      f"message was delivered")
        except Exception as exc:  # noqa: BLE001 - one failure must not strand the rest
            failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
            print(f"flatten_daytrades: {symbol} flatten FAILED "
                  f"({type(exc).__name__}: {exc})")
    for failure in failures:
        print(f"flatten_daytrades: UNRESOLVED {failure}")
    return 1 if failures else 0
