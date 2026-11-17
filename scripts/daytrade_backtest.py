#!/usr/bin/env python3
"""Intraday backtest of the desk's real day-trade pipeline.

Every gate below is the real one, in the real order:
  daytrade_scout.scout_from_bars      (setup classification + OR + VWAP)
  -> zone_analysis.verdict_from_bars  (zone confluence, direction-aware)
  -> daytrade_engine._validate_setup  (odds gate, sizing, R:R)
  -> daytrade_engine._call_risk_controller (atomic caps, long/short books)
  -> simulated fill -> _record_fill, then exit -> _release_position

ONE GATE CANNOT BE REPLAYED AND IS STUBBED, LOUDLY: the Time Machine odds
gate. The tool anchors its replay on the current wall-clock bar
(`daytrade_scout._odds` formats "now" to a 5-minute grid), so no historical
odds exist for any past session. Every rec therefore carries
`odds={"available": False, "reason": ...}` with no "error" key - the shape
the scout itself produces for a short breakdown - so the odds gate neither
blocks nor fails closed here. Read every number in this report as the
performance of the pipeline *minus* its odds gate.

Two documented approximations of the live path, both point-in-time:
  * The zone verdict is computed once per (session, direction) at the
    MIN_BARS bar - the earliest moment the scout would accept a setup -
    rather than at whatever wall-clock moment the live scout happens to run.
  * The regime uses SPY closes up to and including the *prior* session's
    close, so today's own move is not used to judge today.

No look-ahead anywhere else: a session's profile is built from that
session's bars up to the current bar, plus the prior session's profile.

The risk ledger is isolated to a temp dir for the whole run, so the real
ledger is never read or written.

Usage:
  python scripts/daytrade_backtest.py --days 30
  python scripts/daytrade_backtest.py --tickers QQQ,SPY --days 45 --out out.json

Note on data: yfinance serves 5-minute bars for roughly the last 60 days
only, so `--days` above ~60 silently returns fewer sessions. The old
version of this script defaulted to a 2024 window and therefore always
reported zero trades.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
EQUITY = Decimal("100000")

# The live scout's thresholds, imported so the backtest and the desk cannot
# drift: the sweep starts once a session has enough bars to trust a setup.
from scripts.daytrade_scout import (OPENING_BARS, MIN_BARS, classify_direction)

BACKTEST_ODDS = {"available": False,
                 "reason": "time machine not replayable historically"}


# ── Data ───────────────────────────────────────────────────────────────────

def _bars(ticker: str, days: int):
    import yfinance as yf

    frame = yf.Ticker(ticker).history(period=f"{days}d", interval="5m")
    return None if frame is None or frame.empty else frame


def _spy_daily() -> list:
    """SPY daily closes with their dates, newest last. [] on failure.

    Long enough that a 50-day SMA is computable at the *earliest* simulated
    session, so a short fetch window is not silently reported as UNKNOWN
    regime - the day-trade engine does not gate on regime, but the long/short
    split by regime would be meaningless if every session read UNKNOWN.
    """
    import yfinance as yf

    try:
        frame = yf.Ticker("SPY").history(period="2y", interval="1d")
        if frame is None or frame.empty:
            return []
        return [(str(i)[:10], float(c)) for i, c in
                zip(frame.index, frame["Close"].dropna())]
    except Exception:  # noqa: BLE001 - an unreadable SPY is not RISK_ON
        return []


def _regime_for(spy_daily: list, before_date: str) -> str:
    """market_regime over SPY closes up to and including `before_date`.

    `before_date` is the prior session's date, so a session is never judged
    by its own move. Unknown/failed SPY history returns UNKNOWN, which the
    desk treats as not-permission-to-trade.
    """
    from robinhood_tools.market_prices import market_regime

    prefix = [c for d, c in spy_daily if d <= before_date]
    return market_regime(prefix)


# ── Isolation ──────────────────────────────────────────────────────────

@contextmanager
def isolated_ledger():
    """Point the risk controller's module globals at a temp dir."""
    import robinhood_tools.portfolio_risk as pr

    saved = (pr.STATE_FILE, pr.KILL_SWITCH_FILE, pr.LOCK_FILE)
    tmp = Path(tempfile.mkdtemp(prefix="daytrade-bt-"))
    pr.STATE_FILE = tmp / "state.json"
    pr.KILL_SWITCH_FILE = tmp / "KILL_SWITCH"
    pr.LOCK_FILE = tmp / "risk.lock"
    try:
        yield
    finally:
        pr.STATE_FILE, pr.KILL_SWITCH_FILE, pr.LOCK_FILE = saved


# ── Fill simulation ─────────────────────────────────────────────────────

def _simulate_exit(bars: list, entry_idx: int, plan: dict,
                   commission_per_share: float, slippage_bps: float,
                   borrow_bps_per_day: float = 0.0) -> dict | None:
    """Walk bars after entry to the first stop/target touch, or end of day.

    All levels and the size come from the plan the real `_validate_setup`
    produced - this function adds only what a backtest must add: fills and
    costs.
    """
    if entry_idx < OPENING_BARS:
        return None
    direction = plan["direction"]
    entry_price = plan["entry"]
    stop = plan["stop"]
    target = plan["target"]
    qty = plan["qty"]

    slip = slippage_bps / 10000.0
    if direction == "long":
        fill_entry = entry_price * (1 + slip)
    else:
        fill_entry = entry_price * (1 - slip)

    exit_price = exit_reason = None
    exit_idx = None
    for i in range(entry_idx + 1, len(bars)):
        bar = bars[i]
        if direction == "long":
            if bar["low"] <= stop:
                exit_price, exit_reason, exit_idx = stop, "stop", i
                break
            if bar["high"] >= target:
                exit_price, exit_reason, exit_idx = target, "target", i
                break
        else:
            if bar["high"] >= stop:
                exit_price, exit_reason, exit_idx = stop, "stop", i
                break
            if bar["low"] <= target:
                exit_price, exit_reason, exit_idx = target, "target", i
                break
    if exit_price is None:
        exit_price = bars[-1]["close"]
        exit_reason = "eod"
        exit_idx = len(bars) - 1

    if direction == "long":
        fill_exit = exit_price * (1 - slip)
        gross = (fill_exit - fill_entry) * qty
    else:
        fill_exit = exit_price * (1 + slip)
        gross = (fill_entry - fill_exit) * qty

    commission = commission_per_share * qty * 2
    borrow = 0.0
    if direction == "short":
        days_held = (exit_idx - entry_idx) / 78.0  # 78 five-minute bars/day
        borrow = borrow_bps_per_day / 10000.0 * fill_entry * qty * days_held
    net = gross - commission - borrow
    return {
        "direction": direction,
        "setup": plan["setup"],
        "entry": round(fill_entry, 4),
        "stop": stop,
        "target": target,
        "exit": round(fill_exit, 4),
        "exit_reason": exit_reason,
        "qty": qty,
        "net_pnl": round(net, 2),
        "commission": round(commission, 2),
        "borrow_fee": round(borrow, 2),
        "notional": round(fill_entry * qty, 2),
    }


# ── Per-session sweep ──────────────────────────────────────────────────

def _session_bars_list(frame, session: str) -> list:
    return [{"datetime": str(idx), "open": float(r["Open"]),
             "high": float(r["High"]), "low": float(r["Low"]),
             "close": float(r["Close"]), "volume": float(r["Volume"])}
            for idx, r in frame.iterrows() if str(idx)[:10] == session]


def _run_session(ticker: str, frame, session: str, prior: str,
                 commission: float, slippage: float, borrow: float) -> list:
    """Sweep one session, gating each candidate bar through the real pipeline."""
    from scripts import daytrade_engine as de
    from scripts.zone_analysis import verdict_from_bars

    rows = _session_bars_list(frame, session)
    if len(rows) < MIN_BARS:
        return []

    opening = rows[:OPENING_BARS]
    or_high = max(b["high"] for b in opening)
    or_low = min(b["low"] for b in opening)
    vol = [b["volume"] for b in rows]
    tpv = [(b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in rows]

    # The zone verdict is point-in-time at the MIN_BARS bar: the profile is
    # built from this session's bars up to that bar plus the prior session.
    zone_cache: dict[str, str] = {}
    def zone_verdict(direction: str) -> str:
        if direction not in zone_cache:
            try:
                upto = frame.loc[: rows[MIN_BARS - 1]["datetime"]]
                report = verdict_from_bars(upto, session, direction)
            except Exception:  # noqa: BLE001 - a zone bug is never a block
                report = {}
            zone_cache[direction] = report.get("verdict", {}).get(
                "verdict", "neutral")
        return zone_cache[direction]

    trades: list = []
    i = MIN_BARS - 1
    while i < len(rows):
        price = rows[i]["close"]
        cum_pv = sum(tpv[:i + 1])
        cum_vol = sum(vol[:i + 1])
        vwap = cum_pv / cum_vol if cum_vol > 0 else None
        direction = classify_direction(price, vwap, or_low, or_high)

        if direction in ("long", "short"):
            setup = "long_reclaim" if direction == "long" else "short_breakdown"
            rec = {"ticker": ticker, "date": session, "setup": setup,
                   "price": price, "or_low": or_low, "or_high": or_high,
                   "vwap": round(vwap, 2) if vwap else None,
                   "zone_verdict": zone_verdict(direction),
                   "odds": dict(BACKTEST_ODDS)}
            ok, reason, plan = de._validate_setup(rec, EQUITY)
            if ok:
                risk_ok, risk_reason = de._call_risk_controller(plan, EQUITY)
                if risk_ok:
                    de._record_fill(plan)
                    result = _simulate_exit(rows, i, plan, commission,
                                            slippage, borrow)
                    if result:
                        trades.append(result)
                        de._release_position(ticker, direction,
                                             result["notional"])
                        i += 10  # no overlapping trades, as in the live engine
                        continue
            elif reason.startswith("time machine unavailable"):
                # The stubbed odds gate must never fail closed, or the whole
                # backtest would report zero trades.
                pass
        i += 1
    return trades


# ── Aggregation ────────────────────────────────────────────────────────

def _aggregate(ticker: str, trades: list, regime_of) -> dict:
    if not trades:
        return {"ticker": ticker, "trades": 0, "regimes": {}}
    longs = [t for t in trades if t["direction"] == "long"]
    shorts = [t for t in trades if t["direction"] == "short"]
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] < 0]
    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))

    by_regime: dict[str, dict] = {}
    for t in trades:
        r = regime_of(t)
        slot = by_regime.setdefault(r, {"trades": 0, "net_pnl": 0.0})
        slot["trades"] += 1
        slot["net_pnl"] += t["net_pnl"]

    return {
        "ticker": ticker,
        "trades": len(trades),
        "longs": len(longs),
        "shorts": len(shorts),
        "net_pnl": round(sum(t["net_pnl"] for t in trades), 2),
        "commission": round(sum(t["commission"] for t in trades), 2),
        "borrow": round(sum(t["borrow_fee"] for t in trades), 2),
        "win_rate": round(len(wins) / len(trades), 4),
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "regimes": by_regime,
    }


# ── Entry point ────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Day-trade pipeline backtest.")
    ap.add_argument("--tickers",
                    default="QQQ,SPY,NVDA,AAPL,AMD,MSFT,TSLA,META,AMZN")
    ap.add_argument("--days", type=int, default=30,
                    help="fetch window in days (yfinance caps 5m at ~60)")
    ap.add_argument("--commission", type=float, default=0.005)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--borrow-bps", type=float, default=2.5)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    spy_daily = _spy_daily()
    if not spy_daily:
        print("SPY daily history unavailable; regime would be UNKNOWN "
              "desk-wide and the split meaningless - stopping.", file=sys.stderr)
        return 2

    all_trades: list = []
    per_ticker: list = []
    regime_by_key: dict[tuple, str] = {}

    with isolated_ledger():
        for ticker in tickers:
            frame = _bars(ticker, args.days)
            if frame is None:
                print(f"{ticker}: no 5m bars (window {args.days}d)", file=sys.stderr)
                continue
            sessions = sorted({str(i)[:10] for i in frame.index})
            trades: list = []
            for k in range(1, len(sessions)):
                session, prior = sessions[k], sessions[k - 1]
                regime = _regime_for(spy_daily, prior)
                for t in _run_session(ticker, frame, session, prior,
                                      args.commission, args.slippage_bps,
                                      args.borrow_bps):
                    regime_by_key[(ticker, t["entry"], t["exit"])] = regime
                    trades.append(t)
            all_trades.extend(trades)
            per_ticker.append(_aggregate(
                ticker, trades,
                lambda t, _tk=ticker: regime_by_key.get((_tk, t["entry"], t["exit"]), "UNKNOWN")))

    def regime_of(t: dict) -> str:
        for tk in tickers:
            hit = regime_by_key.get((tk, t["entry"], t["exit"]))
            if hit:
                return hit
        return "UNKNOWN"

    result = {
        "generated_at": datetime.now(ET).isoformat(),
        "window_days": args.days,
        "tickers": tickers,
        "equity": float(EQUITY),
        "caveats": [
            "Time Machine odds gate stubbed (not replayable historically)",
            "zone verdict taken at the MIN_BARS bar, not live scout time",
            "regime judged on SPY closes up to the prior session",
        ],
        "per_ticker": per_ticker,
        "totals": _aggregate("ALL", all_trades, regime_of),
    }

    text = json.dumps(result, indent=2)
    if args.out:
        out_path = Path(args.out)
        # A run that finishes computing must not die writing its report:
        # --out is routinely pointed at a new outputs/<subdir>/ path.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"written -> {args.out}")
    else:
        print(text)

    totals = result["totals"]
    if totals.get("trades"):
        for r, slot in sorted(totals["regimes"].items()):
            print(f"regime {r:9} trades={slot['trades']:5} "
                  f"net=${slot['net_pnl']:>12,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
