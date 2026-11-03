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
import os
import subprocess
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


TM_TOOL = (Path(os.path.expanduser("~")) / ".config" / "opencode" / "skills"
           / "trader-timemachine" / "timemachine.py")
FLATTEN_BY = "15:45"  # the desk's flat-by time: replays end where we exit


def _odds(rec: dict) -> dict:
    """Time Machine odds for a live setup (10k replays + every past firing).

    The tool is long-side only (buy: stop < entry < target), so short
    breakdowns pass through with odds recorded as unavailable. Any failure
    is recorded, never raised: the scout itself must not break the session.
    """
    if rec.get("setup") != "long_reclaim":
        return {"available": False, "reason": "time machine is long-side only"}
    if not TM_TOOL.exists():
        return {"available": False, "reason": "timemachine tool not installed"}
    price, stop = rec["price"], rec["or_low"]
    risk = price - stop
    if risk <= 0:
        return {"available": False, "reason": "no risk distance to the range low"}
    target = round(price + 2 * risk, 2)  # 2R, the exit planner's default
    # The tool anchors the replay on the entry bar's close: round down to the
    # last completed 5-minute bar (its grid), formatted like "10:25 AM".
    now = datetime.now(ET)
    bar_min = (now.minute // 5) * 5
    h12 = now.hour % 12 or 12
    entry_time = f"{h12}:{bar_min:02d} {'AM' if now.hour < 12 else 'PM'}"
    try:
        proc = subprocess.run(
            [sys.executable, str(TM_TOOL), "run",
             "--symbol", rec["ticker"], "--date", rec["date"],
             "--entry", str(price), "--stop", str(round(stop, 2)),
             "--target", str(target), "--entry-time", entry_time,
             "--until", FLATTEN_BY, "--json"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=420)
        if proc.returncode != 0:
            return {"available": False,
                    "reason": f"timemachine refused: {(proc.stderr or proc.stdout)[:120]}"}
        d = json.loads(proc.stdout)
        mc, hist = d.get("mc", {}), d.get("history", {})
        return {"available": True,
                "target_first": round(float(mc.get("p_target", 0)), 4),
                "stop_first": round(float(mc.get("p_stop", 0)), 4),
                "neither": round(float(mc.get("p_neither", 0)), 4),
                "avg_r": round(float(mc.get("avg_R", 0)), 3),
                "history_target": hist.get("target"),
                "history_stop": hist.get("stop"),
                "history_triggered": hist.get("triggered"),
                "verdict": d.get("verdict", ""),
                "plan": {"entry": price, "stop": round(stop, 2), "target": target}}
    except Exception as exc:  # noqa: BLE001 - odds never break the session
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


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
    # Live setup: grade it with the Time Machine before the judge sees it.
    base["odds"] = _odds(base)
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
        if rec["setup"] == "range":
            print(f"{ticker}: range ({rec['reason'][:80]})")
            continue
        odds = rec.get("odds", {})
        odds_txt = (f" | odds: target {odds['target_first']:.0%} / stop "
                    f"{odds['stop_first']:.0%} / neither {odds['neither']:.0%}"
                    f" | {odds.get('verdict', '')}") if odds.get("available") \
            else f" | odds: n/a ({odds.get('reason', '')[:50]})"
        print(f"{ticker}: {rec['setup']} - {rec['reason']}{odds_txt}")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    live = [r for r in report["setups"] if r["setup"] != "range"]
    print(f"daytrade_scout: {len(live)} live setup(s) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
