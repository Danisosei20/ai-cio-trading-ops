#!/usr/bin/env python3
"""Paper position guard: enforce stop/target exits on Alpaca paper holdings.

Reads positions (entry + mark from the broker), evaluates the deterministic
guard, and sells triggered positions through the same gated paper flow
(review + ledger + session guard). Never touches live.

  python3 scripts/paper_position_guard.py --check            # report only
  python3 scripts/paper_position_guard.py --execute          # sell triggered
  python3 scripts/paper_position_guard.py --execute --stop-pct 0.05 --target-pct 0.15

Writes outputs/paper/guard.json (read by the desk UI). Stops use the
broker's avg entry price, so thesis stops from research notes do not drift.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.errors import PolicyViolation
from robinhood_tools.paper_flow import SignalOrder, place_signal_order
from robinhood_tools.position_guard import GuardPolicy, evaluate
from robinhood_tools.runtime import build_settings
from robinhood_tools.settings import load_env


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Enforce stop/target exits on paper positions.")
    p.add_argument("--check", action="store_true", help="report only (default)")
    p.add_argument("--execute", action="store_true", help="sell triggered positions")
    p.add_argument("--stop-pct", type=Decimal, default=Decimal("0.08"))
    p.add_argument("--target-pct", type=Decimal, default=Decimal("0.20"))
    p.add_argument("--tickers", default="", help="comma list; default all positions")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    execute = args.execute
    settings = build_settings(args.config, args.env_file)
    if settings.trading_enabled:
        print("REFUSAL: live kill switch open.", file=sys.stderr)
        return 2
    try:
        settings.require_paper_trading()
    except PolicyViolation as exc:
        print(f"REFUSAL: {exc}", file=sys.stderr)
        return 2
    policy = GuardPolicy(stop_pct=args.stop_pct, target_pct=args.target_pct)

    import os

    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport

    env = {**load_env(args.env_file), **os.environ}
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    only = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}

    results = []
    for p in backend.list_positions():
        symbol = str(p.get("symbol", "")).upper()
        if only and symbol not in only:
            continue
        try:
            qty = Decimal(str(p.get("qty", "0")))
            entry = Decimal(str(p.get("avg_entry_price", "0")))
            mark = Decimal(str(p.get("current_price", "0")))
            if qty <= 0:
                continue
            ev = evaluate(mark, entry, policy)
            rec = {"symbol": symbol, "qty": str(qty), "entry": str(entry),
                   "mark": str(mark), "return_pct": f"{ev.return_pct:.2%}",
                   "stop": str(ev.stop_price.quantize(Decimal("0.01"))),
                   "target": str(ev.target_price.quantize(Decimal("0.01"))),
                   "signal": ev.signal, "action": "hold"}
            if ev.signal != "HOLD" and execute:
                result = place_signal_order(
                    settings=settings, snapshot=None,
                    order=SignalOrder(symbol=symbol, side="sell", quantity=abs(qty),
                                      limit_price=mark.quantize(Decimal("0.01"))),
                    env=env)
                rec.update({"action": "sell_placed", "order_id": result["order_id"],
                            "status": result["status"]})
            results.append(rec)
            print(json.dumps(rec))
        except (PolicyViolation, InvalidOperation, KeyError) as exc:
            rec = {"symbol": symbol, "action": "error", "reason": f"{type(exc).__name__}: {exc}"}
            results.append(rec)
            print(json.dumps(rec))

    out = Path("outputs/paper/guard.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"policy": {"stop_pct": str(policy.stop_pct),
                                           "target_pct": str(policy.target_pct)},
                               "positions": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
