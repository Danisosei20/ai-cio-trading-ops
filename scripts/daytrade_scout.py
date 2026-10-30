#!/usr/bin/env python3
"""Intraday day-trade scout: opening range + VWAP + zone confluence.

Owner directive 2026-10-30: day trade liquid names (QQQ and friends) for
short-term quick profit. This scout PROPOSES intraday setups - it never
trades. Each candidate is classified:

  long_reclaim    price reclaimed VWAP, holds above the opening-range low,
                  and zone analysis does not block longs
  short_breakdown mirror image (long-only book by default; shorts recorded
                  for the judge)
  range           no setup - skipped

Outputs outputs/daytrade/<date>.json. Consumed by the 10:30 day-trade run
(paper_auto --day-trade) and flattened by 15:45 ET.

Usage:
  python scripts/daytrade_scout.py                       # default watchlist
  python scripts/daytrade_scout.py --tickers QQQ,SPY,TSLA
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
WATCHLIST = ("QQQ", "SPY", "NVDA", "AAPL", "AMD", "MSFT", "TSLA", "META", "AMZN")
OPENING_BARS = 6          # first 30 minutes (5m bars)
MIN_BARS = 12             # need ~1h of trade before trusting a reclaim


def _bars(ticker: str):
    import yfinance as yf

    frame = yf.Ticker(ticker).history(period="1d", interval="5m")
    return frame if frame is not None and not frame.empty else None


def scout_one(ticker: str) -> dict:
    frame = _bars(ticker)
    today = datetime.now(ET).date().isoformat()
    if frame is None or len(frame) < OPENING_BARS:
        # Pre-open or data outage: the day-trade run skips; nothing is faked.
        return {"ticker": ticker, "date": today, "setup": "range",
                "reason": f"only {0 if frame is None else len(frame)} bars; "
                          "market not open yet or data missing"}
    bars = frame[[str(i)[:10] == today for i in frame.index]]
    if len(bars) < OPENING_BARS:
        bars = frame  # last partial/prior session as reference
    opening = bars.iloc[:OPENING_BARS]
    or_high = float(opening["High"].max())
    or_low = float(opening["Low"].min())
    typical = (bars["High"] + bars["Low"] + bars["Close"]) / 3
    volume = bars["Volume"].astype(float)
    vwap = float((typical * volume).sum() / volume.sum()) if volume.sum() else None
    price = float(bars["Close"].iloc[-1])
    base = {"ticker": ticker, "date": today, "price": round(price, 2),
            "vwap": round(vwap, 2) if vwap else None,
            "or_high": round(or_high, 2), "or_low": round(or_low, 2),
            "bars": len(bars)}

    try:
        from scripts.zone_analysis import analyze

        zone = analyze(ticker)
        base["zone_verdict"] = zone.get("verdict", {}).get("verdict", "neutral")
        base["zone_reasons"] = zone.get("verdict", {}).get("reasons", [])
    except Exception:  # noqa: BLE001 - the scout itself never crashes
        base["zone_verdict"] = "neutral"
        base["zone_reasons"] = ["zone analysis unavailable"]

    thin = len(bars) < MIN_BARS
    if thin:
        base.update({"setup": "range",
                     "reason": f"only {len(bars)} bars - session too young"})
        return base
    if vwap is None:
        base.update({"setup": "range", "reason": "no volume for VWAP"})
        return base
    if price > vwap and price >= or_low and base["zone_verdict"] != "long_blocked":
        base.update({"setup": "long_reclaim",
                     "reason": f"price {price:.2f} above VWAP {vwap:.2f}, holding "
                               f"opening-range low {or_low:.2f}"})
    elif price < vwap and price <= or_high and base["zone_verdict"] != "short_blocked":
        base.update({"setup": "short_breakdown",
                     "reason": f"price {price:.2f} below VWAP {vwap:.2f}, under "
                               f"opening-range high {or_high:.2f}"})
    else:
        base.update({"setup": "range",
                     "reason": f"price {price:.2f} vs VWAP {vwap:.2f}; "
                               "no directional reclaim"})
    return base


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Intraday day-trade setup scout.")
    p.add_argument("--tickers", default=",".join(WATCHLIST))
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    today = datetime.now(ET).date().isoformat()
    out = Path(args.out) if args.out else ROOT / "outputs" / "daytrade" / f"{today}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {"date": today, "generated_at": datetime.now(ET).isoformat(),
              "setups": []}
    for ticker in [t.strip().upper() for t in args.tickers.split(",") if t.strip()]:
        rec = scout_one(ticker)
        report["setups"].append(rec)
        if rec["setup"] != "range":
            print(f"{ticker}: {rec['setup']} - {rec['reason']}")
        else:
            print(f"{ticker}: range ({rec['reason'][:80]})")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    live = [r for r in report["setups"] if r["setup"] != "range"]
    print(f"daytrade_scout: {len(live)} live setup(s) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
