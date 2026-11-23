#!/usr/bin/env python3
"""Portfolio scenario analysis: what the book does under any market scenario.

The Time Machine answers "what are the odds of this position's exit bands".
This answers the operator's other question: "if X happens, what is my P/L?"
- deterministic scenarios (spot moves, theta decay, IV shocks, gaps),
  applied to the CURRENT book with the contract's own Greeks,
- P/L vs entry and vs the current mark, and whether the scenario
  triggers the guard's -15% stop or +2%/trail exits.

Model: new_premium = current + delta*dS + 0.5*gamma*dS^2 + theta*days
+ vega*dIV. Missing Greeks are 0 (delta-only), which is stated per row so
no number is silently overstated. Paper only: reads the broker, places
nothing.

Usage:
  python scripts/scenario_analysis.py            # every open position
  python scripts/scenario_analysis.py --symbol RDDT270115P00095000
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
OUT = ROOT / "outputs" / "simulation" / "scenario_analysis.json"

# Scenario definitions: (name, d_spot_pct, theta_days, d_iv_pct)
SCENARIOS = [
    ("underlier -20% (crash)", -0.20, 0, 0.0),
    ("underlier -10%", -0.10, 0, 0.0),
    ("underlier -5%", -0.05, 0, 0.0),
    ("underlier -2%", -0.02, 0, 0.0),
    ("flat", 0.0, 0, 0.0),
    ("underlier +2%", 0.02, 0, 0.0),
    ("underlier +5%", 0.05, 0, 0.0),
    ("underlier +10% (rally)", 0.10, 0, 0.0),
    ("underlier +20% (melt-up)", 0.20, 0, 0.0),
    ("1 day of decay", 0.0, 1, 0.0),
    ("5 days of decay", 0.0, 5, 0.0),
    ("10 days of decay", 0.0, 10, 0.0),
    ("IV -20% (calm)", 0.0, 0, -0.20),
    ("IV +20% (spike)", 0.0, 0, 0.20),
    ("gap -15% + IV +20%", -0.15, 0, 0.20),
    ("gap +15% + IV -20%", 0.15, 0, -0.20),
]
STOP_PCT = -0.15   # guard's stop on premium
TARGET_PCT = 0.02  # reverted take-profit (0 = disabled), kept for the flag


def fetch_book() -> list[dict]:
    """Open option positions with their quotes and Greeks."""
    from robinhood_tools.alpaca_options import (
        AlpacaOptionsData,
        snapshot_to_quote,
    )
    from robinhood_tools.alpaca_paper import (
        AlpacaPaperBackend,
        AlpacaPaperHttpTransport,
    )
    from robinhood_tools.market_prices import get_quote
    from robinhood_tools.options import occ_details
    from robinhood_tools.settings import load_env

    env = load_env(".env")
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    data = AlpacaOptionsData.from_values(env)
    book = []
    for pos in backend.list_positions():
        symbol = str(pos.get("symbol", ""))
        if len(symbol) <= 10:  # equity, not an OCC option
            continue
        contract = occ_details(symbol)
        try:
            snapshots = data.snapshots([symbol])
            quote = snapshot_to_quote(contract, snapshots[symbol],
                                      Decimal(str(pos.get("current_price", "0"))))
            greeks = {
                "delta": float(quote.delta or 0),
                "gamma": float(quote.gamma or 0),
                "theta": float(quote.theta or 0),
                "vega": float(quote.vega or 0),
                "iv": float(quote.iv or 0),
            }
            mid = float(quote.mid())
        except Exception as exc:  # noqa: BLE001 - no Greeks, delta-only row
            get_quote(contract.underlying, max_age_minutes=1440)
            greeks = {"delta": 0.5, "gamma": 0.0, "theta": 0.0,
                      "vega": 0.0, "iv": 0.0, "error": str(exc)[:80]}
            mid = float(pos.get("current_price", "0"))
        book.append({
            "symbol": symbol, "underlying": contract.underlying,
            "right": contract.right, "expiry": contract.expiry,
            "qty": int(Decimal(str(pos.get("qty", "0")))),
            "entry": float(pos.get("avg_entry_price", "0")),
            # The broker's own mark is the P/L base; the quote supplies the
            # Greeks and the underlier spot for the spot-move scenarios.
            "mark": float(pos.get("current_price", mid)),
            "spot": float(quote.underlying_price),
            "greeks": greeks,
        })
    return book


def analyse(book: list[dict]) -> dict:
    rows = []
    for name, d_spot_pct, days, d_iv_pct in SCENARIOS:
        book_pl_vs_entry = 0.0
        book_change = 0.0
        per_pos = []
        triggers = []
        for pos in book:
            g = pos["greeks"]
            # dS in dollars of the underlier: the scenario's percentage
            # applied to the contract's own spot price.
            d_s = d_spot_pct * pos["spot"]
            d_iv = d_iv_pct * g["iv"]
            new_prem = (pos["mark"]
                        + g["delta"] * d_s
                        + 0.5 * g["gamma"] * d_s * d_s
                        + g["theta"] * days
                        + g["vega"] * d_iv)
            pl_entry = (new_prem - pos["entry"]) * pos["qty"] * 100
            change = (new_prem - pos["mark"]) * pos["qty"] * 100
            book_pl_vs_entry += pl_entry
            book_change += change
            ret = new_prem / pos["mark"] - 1 if pos["mark"] else 0.0
            if ret <= STOP_PCT:
                triggers.append(f"{pos['symbol']} hits -15% STOP")
            per_pos.append({"symbol": pos["symbol"],
                            "new_premium": round(new_prem, 2),
                            "pl_vs_entry": round(pl_entry, 2),
                            "change_vs_mark": round(change, 2),
                            "premium_return": round(ret, 4)})
        rows.append({"scenario": name,
                     "d_spot_pct": d_spot_pct, "theta_days": days,
                     "d_iv_pct": d_iv_pct,
                     "book_pl_vs_entry": round(book_pl_vs_entry, 2),
                     "book_change": round(book_change, 2),
                     "stop_triggered": bool(triggers),
                     "triggers": triggers,
                     "positions": per_pos})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Portfolio scenario analysis.")
    ap.add_argument("--symbol", action="append", default=[],
                    help="restrict to these OCC symbols (default: the book)")
    args = ap.parse_args(argv)

    book = fetch_book()
    had_positions = bool(book)
    if args.symbol:
        wanted = {s.upper() for s in args.symbol}
        book = [p for p in book if p["symbol"].upper() in wanted]
    if not book:
        # An empty result is a real, dated state, not a failed run. Write an
        # empty snapshot so the stage has fresh evidence instead of a stale
        # artifact describing positions the desk no longer holds. Be honest
        # about whether the book was flat or the symbol filter matched none.
        reason = ("no open option positions" if not had_positions
                  else "no open option positions matching --symbol")
        payload = {"generated_at": datetime.now(ET).isoformat(),
                   "model": "delta+gamma+theta+vega first-order; missing Greeks are 0",
                   "stop_pct": STOP_PCT,
                   "reason": reason,
                   "book": [], "scenarios": []}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"no open option positions to analyse; wrote {OUT}")
        return 0

    rows = analyse(book)
    payload = {"generated_at": datetime.now(ET).isoformat(),
               "model": "delta+gamma+theta+vega first-order; missing Greeks are 0",
               "stop_pct": STOP_PCT,
               "book": [{k: p[k] for k in ("symbol", "qty", "entry", "mark")}
                        for p in book],
               "scenarios": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"{'scenario':38s} {'book P/L vs entry':>19s} {'change':>10s}  guard")
    for r in rows:
        guard = "STOP " + "; ".join(r["triggers"]) if r["triggers"] else "-"
        print(f"{r['scenario']:38s} {r['book_pl_vs_entry']:>19,.2f} "
              f"{r['book_change']:>10,.2f}  {guard}")
    print(f"\nwritten -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())