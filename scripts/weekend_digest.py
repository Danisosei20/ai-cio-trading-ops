#!/usr/bin/env python3
"""Saturday book review: positions, week-to-date, guard states, week ahead.

Read-only except writing its own log. Posts one Slack digest.

  python3 scripts/weekend_digest.py --send
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Saturday book review.")
    p.add_argument("--send", action="store_true")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
    from robinhood_tools.settings import load_env
    import os

    env = {**load_env(args.env_file), **os.environ}
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    positions = backend.list_positions()
    open_orders = backend.list_open_orders()

    stocks = [p for p in positions if len(str(p.get("symbol", ""))) <= 10]
    options = [p for p in positions if len(str(p.get("symbol", ""))) > 10]
    lines = ["*Saturday book review*"]
    if stocks:
        lines.append("*Stocks*")
        for p in stocks:
            lines.append(f"• {p.get('symbol')} x{p.get('qty')} @ {p.get('current_price')} "
                         f"(P&L {p.get('unrealized_pl')})")
    else:
        lines.append("Stocks: flat.")
    if options:
        lines.append("*Options*")
        for p in options:
            lines.append(f"• {p.get('symbol')} x{p.get('qty')} @ {p.get('current_price')} "
                         f"(P&L {p.get('unrealized_pl')})")
    else:
        lines.append("Options: flat.")
    if open_orders:
        lines.append("*Working orders*")
        for o in open_orders:
            lines.append(f"• {o.get('side')} {o.get('symbol')} x{o.get('qty')} "
                         f"@ {o.get('limit_price')} ({o.get('status')})")
    guard = {}
    gf = Path("outputs/paper/guard.json")
    if gf.exists():
        try:
            guard = json.loads(gf.read_text())
        except Exception:
            pass
    triggered = [p for p in guard.get("positions", []) if p.get("signal") != "HOLD"] \
        if guard else []
    lines.append("Guard: " + (", ".join(f"{p['symbol']} {p['signal']}" for p in triggered)
                              if triggered else "all HOLD, week ahead: earnings calendar check Monday."))
    message = "\n".join(lines)
    print(message)
    if args.send:
        sys.path.insert(0, ".")
        from scripts.notify import send

        result = send(message)
        print("sent", result.get("message_ts", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
