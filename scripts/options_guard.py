#!/usr/bin/env python3
"""Options position guard: time, profit, and loss exits for long contracts.

Evaluates every open OCC position each run:
  EXPIRE  DTE <= --max-dte (default 2) — never hold into expiry weekend
  TARGET  premium up >= --target-pct (default 0.50)
  STOP    premium down <= ---stop-pct (default 0.50)
  STALE   held >= --max-hold-days (default 5) without hitting target
Exits sell-to-close through review + ledger + session guard. Paper only.

  python3 scripts/options_guard.py --check
  python3 scripts/options_guard.py --execute
State: outputs/paper/options_guard.json (first-seen dates).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.errors import PolicyViolation  # noqa: E402
from robinhood_tools.options import occ_details  # noqa: E402
from robinhood_tools.options_flow import close_long_option  # noqa: E402
from robinhood_tools.runtime import build_settings  # noqa: E402

STATE_FILE = Path("outputs/paper/options_guard.json")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Exit guard for long option positions.")
    p.add_argument("--check", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--target-pct", type=Decimal, default=Decimal("0.50"))
    p.add_argument("--stop-pct", type=Decimal, default=Decimal("0.50"))
    p.add_argument("--max-dte", type=int, default=2)
    p.add_argument("--max-hold-days", type=int, default=5)
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def decide(entry_cost: Decimal, mark_value: Decimal, dte: int, hold_days: int,
           target_pct: Decimal, stop_pct: Decimal,
           max_dte: int, max_hold_days: int) -> tuple[str, str]:
    """Pure exit decision. Returns (action, reason)."""
    if entry_cost <= 0:
        raise PolicyViolation("Entry cost must be positive.")
    ret = mark_value / entry_cost - 1
    if dte <= max_dte:
        return "EXPIRE", f"DTE {dte} <= {max_dte}"
    if ret >= target_pct:
        return "TARGET", f"premium {ret:.0%} >= {target_pct:.0%}"
    if ret <= -stop_pct:
        return "STOP", f"premium {ret:.0%} <= -{stop_pct:.0%}"
    if hold_days >= max_hold_days:
        return "STALE", f"held {hold_days}d without target"
    return "HOLD", f"premium {ret:.0%}, DTE {dte}, day {hold_days}"


def main(argv=None) -> int:
    args = parse_args(argv)
    settings = build_settings(args.config, args.env_file)
    if settings.trading_enabled:
        print("REFUSAL: live kill switch open.", file=sys.stderr)
        return 2

    import os

    from robinhood_tools.alpaca_options import AlpacaOptionsData
    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
    from robinhood_tools.settings import load_env

    env = {**load_env(args.env_file), **os.environ}
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    data = AlpacaOptionsData.from_values(env)
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    today = date.today()

    results = []
    for p in backend.list_positions():
        symbol = str(p.get("symbol", ""))
        if len(symbol) <= 10:
            continue  # not an OCC contract
        rec = {"symbol": symbol}
        try:
            contract = occ_details(symbol)
            qty = int(float(str(p.get("qty", "0"))))
            if qty <= 0:
                continue
            cost = abs(Decimal(str(p.get("cost_basis", "0"))))
            dte = (date.fromisoformat(contract.expiry) - today).days
            seen = state.get(symbol, {})
            if "first_seen" not in seen:
                seen = {"first_seen": today.isoformat(), "cost": str(cost)}
                state[symbol] = seen
            hold_days = (today - date.fromisoformat(seen["first_seen"])).days
            snaps = data.snapshots([symbol])
            snap = snaps.get(symbol) or {}
            bid = Decimal(str((snap.get("latestQuote") or {}).get("bp", "0")))
            mark_value = bid * qty * 100
            action, reason = decide(cost, mark_value, dte, hold_days,
                                   args.target_pct, args.stop_pct,
                                   args.max_dte, args.max_hold_days)
            rec.update({"qty": qty, "dte": dte, "hold_days": hold_days,
                        "return": f"{(mark_value / cost - 1):.0%}" if cost > 0 else None,
                        "signal": action, "reason": reason})
            if action != "HOLD" and args.execute:
                from robinhood_tools.alpaca_options import snapshot_to_quote  # noqa: E402
                from robinhood_tools.options import occ_details as _occ  # noqa: E402
                from robinhood_tools.alpaca_market_data import (  # noqa: E402
                    AlpacaMarketDataHttpClient)
                from robinhood_tools.market_prices import get_quote as _spot

                contract = _occ(symbol)
                snaps = data.snapshots([symbol])
                snap = snaps.get(symbol) or {}
                lq = snap.get("latestQuote") or {}
                if not lq.get("bp"):
                    raise PolicyViolation(f"No bid for {symbol}; cannot exit into air.")
                under = _spot(contract.underlying,
                              data_client=AlpacaMarketDataHttpClient.from_values(env)).price
                quote = snapshot_to_quote(contract, snap, under)
                result = close_long_option(
                    settings=settings, quote=quote, contracts=qty, database=None,
                    service=None, today=today)
                if result["status"] in {"queued", "confirmed", "filled",
                                        "partially_filled", "accepted"}:
                    rec.update({"exit_action": "sell_placed",
                                "order_id": result["order_id"], "status": result["status"]})
                    state.pop(symbol, None)
                else:
                    rec.update({"exit_action": "not_accepted",
                                "status": result["status"]})
            results.append(rec)
            print(json.dumps(rec))
        except (PolicyViolation, InvalidOperation, KeyError, ValueError) as exc:
            results.append({"symbol": symbol, "action": "error",
                            "reason": f"{type(exc).__name__}: {exc}"})
            print(json.dumps(results[-1]))
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"updated": today.isoformat(), "tracked": state,
                                      "evaluated": results}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
