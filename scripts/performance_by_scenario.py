#!/usr/bin/env python3
"""The desk's REAL track record, split by scenario.

Every other test in this suite is a simulation or a gate check. This one
uses the only evidence that matters: the fills the broker actually gave
us (reconciled into order_fills since 2026-11-16). It pairs the desk's own
fills into round-trips and asks the honest question - does this desk make
money in ANY scenario, or only some?

Attribution axes: market regime at entry (SPY vs its 50-day mean - the
desk's own definition), contract side (call/put), weekday, and hold
length. Sample sizes are printed next to every number, because 33
round-trips is a track record, not a proof.

Usage:
  python scripts/performance_by_scenario.py
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
OUT = ROOT / "outputs" / "simulation" / "performance_by_scenario.json"


def round_trips_with_dates(fills: list[dict]) -> list[dict]:
    """FIFO-pair the desk's fills, keeping entry date and contract side."""
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in fills:
        symbol = str(row.get("symbol", "")).strip().upper()
        if symbol:
            by_symbol[symbol].append(row)

    def right_of(symbol: str) -> str:
        return "put" if len(symbol) > 10 and symbol[-9] == "P" else (
            "call" if len(symbol) > 10 else "equity")

    def mult_of(symbol: str) -> Decimal:
        return Decimal("100") if len(symbol) > 10 else Decimal("1")

    trips = []
    for symbol, rows in by_symbol.items():
        rows = sorted(rows, key=lambda r: str(r.get("filled_at", "")))
        buys: list[tuple[Decimal, Decimal, str]] = []  # qty, price, date
        for row in rows:
            side = str(row.get("side", "")).lower()
            try:
                qty = Decimal(str(row.get("quantity", "0")))
                price = Decimal(str(row.get("price", "0")))
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            if side == "buy":
                buys.append((qty, price, str(row.get("filled_at", ""))[:10]))
                continue
            if side != "sell":
                continue
            remaining = qty
            sell_value = price * qty
            cost = Decimal("0")
            closed_qty = Decimal("0")
            entry_day = None
            while remaining > 0 and buys:
                bqty, bprice, bdate = buys[0]
                take = min(bqty, remaining)
                cost += take * bprice
                closed_qty += take
                entry_day = entry_day or bdate
                remaining -= take
                if take >= bqty:
                    buys.pop(0)
                else:
                    buys[0] = (bqty - take, bprice, bdate)
            if closed_qty <= 0:
                continue
            exit_day = str(row.get("filled_at", ""))[:10]
            pnl = ((sell_value - cost) * mult_of(symbol)).quantize(Decimal("0.01"))
            hold = None
            if entry_day and exit_day:
                try:
                    hold = (date.fromisoformat(exit_day)
                            - date.fromisoformat(entry_day)).days
                except ValueError:
                    hold = None
            trips.append({"symbol": symbol,
                          "underlier": symbol[:6].rstrip("0123456789CP")
                          if len(symbol) > 10 else symbol,
                          "right": right_of(symbol), "qty": str(closed_qty),
                          "entry_day": entry_day, "exit_day": exit_day,
                          "hold_days": hold, "pnl": pnl})
    trips.sort(key=lambda t: t["exit_day"] or "")
    return trips


def regime_by_day(spy_closes: list[tuple[str, float]]) -> dict[str, str]:
    """{date: RISK_ON/RISK_OFF/CHOP/UNKNOWN} from SPY closes."""
    from robinhood_tools.market_prices import market_regime

    out: dict[str, str] = {}
    for i, (day, _close) in enumerate(spy_closes):
        # Full history up to and including this day: market_regime()
        # needs >=51 closes, and slicing to the last 50 made every single
        # day read UNKNOWN (the whole axis, silently).
        out[day] = market_regime([c for _d, c in spy_closes[:i + 1]])
    return out


def summarise(trips: list[dict], key) -> dict:
    groups: dict[str, list[Decimal]] = defaultdict(list)
    for t in trips:
        groups[str(key(t))].append(t["pnl"])
    out = {}
    for name, pnls in sorted(groups.items()):
        wins = sum(1 for p in pnls if p > 0)
        out[name] = {"n": len(pnls), "net": float(sum(pnls)),
                     "wins": wins, "losses": len(pnls) - wins,
                     "win_rate": round(wins / len(pnls), 3) if pnls else 0.0,
                     "avg": round(float(statistics.fmean(pnls)), 2) if pnls else 0.0,
                     "best": float(max(pnls)), "worst": float(min(pnls))}
    return out


def main(argv=None) -> int:
    from robinhood_tools.database import CioDatabase
    from robinhood_tools.runtime import build_settings

    settings = build_settings("config/approval_routes.json", ".env")
    db = CioDatabase(settings.database_path)
    trips = round_trips_with_dates(db.all_fill_rows())
    if not trips:
        print("no closed round-trips in the ledger")
        return 0

    # SPY closes for the regime labels (one fetch, ~90 days covers the book).
    import yfinance as yf  # noqa: PLC0415

    frame = yf.Ticker("SPY").history(period="120d", interval="1d")
    spy = [(str(i)[:10], float(v)) for i, v in
           zip(frame.index, frame["Close"].dropna())] if frame is not None else []
    regimes = regime_by_day(spy) if spy else {}

    for t in trips:
        t["regime"] = regimes.get(t["entry_day"] or "", "UNKNOWN")
        t["weekday"] = (date.fromisoformat(t["entry_day"]).strftime("%a")
                        if t["entry_day"] else "?")

    total = float(sum(t["pnl"] for t in trips))
    report = {
        "generated_at": datetime.now(ET).isoformat(),
        "round_trips": len(trips),
        "net_realized": round(total, 2),
        "by_regime": summarise(trips, lambda t: t["regime"]),
        "by_side": summarise(trips, lambda t: t["right"]),
        "by_weekday": summarise(trips, lambda t: t["weekday"]),
        "by_hold": summarise(trips, lambda t: (
            "0d" if (t["hold_days"] or 0) == 0 else
            "1-3d" if (t["hold_days"] or 0) <= 3 else
            "4-10d" if (t["hold_days"] or 0) <= 10 else "10d+")),
        "worst_5": [{"symbol": t["symbol"], "pnl": float(t["pnl"]),
                     "regime": t["regime"], "side": t["right"],
                     "exit_day": t["exit_day"]}
                    for t in sorted(trips, key=lambda t: t["pnl"])[:5]],
        "best_5": [{"symbol": t["symbol"], "pnl": float(t["pnl"]),
                    "regime": t["regime"], "side": t["right"],
                    "exit_day": t["exit_day"]}
                   for t in sorted(trips, key=lambda t: -t["pnl"])[:5]],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"round-trips: {len(trips)} | net realized: ${total:,.2f}\n")
    for axis in ("by_regime", "by_side", "by_weekday", "by_hold"):
        print(f"{axis}:")
        for name, s in report[axis].items():
            print(f"  {name:10s} n={s['n']:<3d} net=${s['net']:>10,.2f} "
                  f"win={s['win_rate']:>5.0%} avg=${s['avg']:>8,.2f}")
        print()
    print(f"written -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())