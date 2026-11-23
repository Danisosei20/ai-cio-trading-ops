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
    """Never sell more than the trade's own recorded quantity at the broker."""
    owned = owned_quantity(rec)
    if owned is None:
        return Decimal("0")
    return min(owned, held) if held > 0 else Decimal("0")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Flatten day-trade positions before the close.")
    p.add_argument("--check", action="store_true", help="report only; sell nothing")
    p.add_argument("--date", default=None, help="trade date (default today ET)")
    args = p.parse_args(argv)

    today = args.date or datetime.now(ET).date().isoformat()
    records = [r for r in _today_records(today)
               if str(r.get("action", "")).endswith("_placed")]
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

    for rec in records:
        symbol = str(rec.get("ticker", "")).upper()
        held = positions.get(symbol, Decimal("0"))
        if held <= 0:
            print(f"flatten_daytrades: {symbol} already flat")
            continue
        owned = owned_quantity(rec)
        if owned is None:
            print(f"flatten_daytrades: {symbol} no owned quantity recorded — "
                  f"refusing to sell the whole broker position")
            continue
        qty = sellable_quantity(rec, held)
        if qty <= 0:
            print(f"flatten_daytrades: {symbol} no owned quantity available at broker")
            continue
        if qty < held:
            print(f"flatten_daytrades: {symbol} broker holds {held}, this trade owns {qty}; "
                  f"leaving {held - qty} for its owning strategy")
        if args.check:
            print(f"flatten_daytrades: {symbol} still held {held} -> would flatten")
            continue
        try:
            from robinhood_tools.market_prices import get_quote

            price = get_quote(symbol).price
            result = place_signal_order(
                settings=settings, snapshot=None,
                order=SignalOrder(symbol=symbol, side="sell",
                                  quantity=qty,
                                  limit_price=price.quantize(Decimal("0.01"))),
                env_path=".env")
            print(f"flatten_daytrades: {symbol} sell {qty} @ {price} -> "
                  f"{result['status']} (order {result['order_id']})")
            # The Slack alert is best-effort; its outcome is now recorded by
            # place_signal_order. A flatten the operator cannot see is a
            # flatten they cannot audit, so say so plainly when it happens.
            if result.get("alert") == "failed":
                print(f"flatten_daytrades: {symbol} ALERT FAILED - the sell "
                      f"is in the ledger and at the broker, but no Slack "
                      f"message was delivered")
        except Exception as exc:  # noqa: BLE001 - one failure must not strand the rest
            print(f"flatten_daytrades: {symbol} flatten FAILED "
                  f"({type(exc).__name__}: {exc})")
    return 0
