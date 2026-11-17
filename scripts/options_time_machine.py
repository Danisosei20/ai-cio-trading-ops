#!/usr/bin/env python3
"""Time Machine for the options book: the odds of a position's exit bands.

The desk's Time Machine replays equity day trades (5-minute bars, last 60
days). Every position the desk actually HOLDS is a long option, and those
had no odds at all - so the +2% target / -15% stop the guard enforces was
never simulated before the position was opened. This module simulates the
contract the desk owns, right now, and reports P(target first) /
P(stop first) / P(neither) plus the dollar P/L distribution.

Model, stated plainly because it is an approximation:
  * The underlier follows a geometric random walk over trading days to
    the horizon, with sigma from the underlier's own recent daily closes
    (realized vol, annualized), falling back to the contract's quoted IV.
  * The option's premium moves DELTA-ONLY: premium_t = premium_0 +
    delta x (S_t - S_0). Gamma, vega and theta decay are ignored, so the
    bands' probabilities are first-order estimates, not a pricing model.
  * Each path stops at the FIRST band touched, exactly like the guard's
    OCO brackets do in the broker.

Usage:
  python scripts/options_time_machine.py --symbol RDDT270115P00095000
  python scripts/options_time_machine.py --book          # every open option
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
OUT = ROOT / "outputs" / "simulation" / "options_time_machine.json"
BLACKOUT_DAYS = 10  # horizon: a position older than this is not "now"


def annualized_vol(closes: list[float]) -> float | None:
    """Annualized vol from daily log returns (0 when fewer than 20 bars)."""
    if len(closes) < 20:
        return None
    returns = [math.log(closes[i] / closes[i - 1])
               for i in range(1, len(closes))
               if closes[i - 1] > 0 and closes[i] > 0]
    if len(returns) < 19:
        return None
    return statistics.stdev(returns) * math.sqrt(252)


def simulate_option_odds(*, premium0: float, delta: float, spot0: float,
                         sigma: float, days: int, paths: int,
                         target_pct: float, stop_pct: float,
                         seed: int) -> dict:
    """Simulated exit-band odds. Pure and seeded: same inputs, same answer."""
    target = premium0 * (1 + target_pct)
    stop = premium0 * (1 - stop_pct)
    rng = random.Random(seed)
    dt = 1.0 / 252.0
    drift_step = -0.5 * sigma * sigma * dt
    diff_step = sigma * math.sqrt(dt)
    target_first = stop_first = neither = 0
    terminal: list[float] = []
    for _ in range(paths):
        spot = spot0
        premium = premium0
        hit = 0
        for _day in range(max(days, 1)):
            spot *= math.exp(drift_step + diff_step * rng.gauss(0, 1))
            premium = premium0 + delta * (spot - spot0)
            if premium >= target:
                hit = 1
                break
            if premium <= stop:
                hit = -1
                break
        if hit == 1:
            target_first += 1
        elif hit == -1:
            stop_first += 1
        else:
            neither += 1
            terminal.append(premium)
    p_target, p_stop = target_first / paths, stop_first / paths
    # Expected value of the exit policy on premium, per round trip. The
    # guard takes +target_pct or -stop_pct, so a policy pays nothing for
    # surviving to expiry: EV = pT*target + pS*(-stop).
    ev_pct = p_target * target_pct - p_stop * stop_pct
    return {
        "paths": paths,
        "target_first": p_target,
        "stop_first": p_stop,
        "neither": neither / paths,
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "ev_pct_on_premium": round(ev_pct, 5),
        "ev_usd_per_contract": round(ev_pct * premium0 * 100, 2),
        "terminal_premium_median": (statistics.median(terminal)
                                    if terminal else None),
    }


def fetch_quote(symbol: str, *, max_age_minutes: int = 5):
    """(OptionQuote, sigma) for one OCC symbol: live quote + realized vol."""
    from robinhood_tools.alpaca_options import (
        AlpacaOptionsData,
        snapshot_to_quote,
    )
    from robinhood_tools.market_prices import get_quote
    from robinhood_tools.options import occ_details
    from robinhood_tools.settings import load_env

    env = load_env(".env")
    contract = occ_details(symbol)
    data = AlpacaOptionsData.from_values(env)
    snapshots = data.snapshots([symbol])
    if symbol not in snapshots:
        raise KeyError(f"no Alpaca snapshot for {symbol}")
    spot_q = get_quote(contract.underlying, max_age_minutes=max_age_minutes)
    quote = snapshot_to_quote(contract, snapshots[symbol], spot_q.price)

    import yfinance as yf  # noqa: PLC0415

    frame = yf.Ticker(contract.underlying).history(period="60d", interval="1d")
    closes = [float(v) for v in frame["Close"].dropna()] if frame is not None else []
    sigma = annualized_vol(closes)
    if sigma is None or sigma <= 0:
        iv = float(quote.iv) if quote.iv else None
        sigma = iv if iv and iv > 0 else None
    return quote, sigma, contract


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Options-book Time Machine.")
    ap.add_argument("--symbol", action="append", default=[],
                    help="OCC symbol; repeatable. --book reads the broker.")
    ap.add_argument("--book", action="store_true",
                    help="simulate every open long option position")
    ap.add_argument("--paths", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--target-pct", type=float, default=0.02)
    ap.add_argument("--stop-pct", type=float, default=0.15)
    ap.add_argument("--days", type=int, default=None,
                    help="horizon in trading days (default: min(DTE, 10)")
    ap.add_argument("--allow-stale", action="store_true",
                    help="simulate off the last quote even when the market is "
                         "closed (the trading path still fails closed on "
                         "stale quotes; a simulation is not a decision)")
    args = ap.parse_args(argv)
    max_age = 1440 if args.allow_stale else 5

    symbols = list(args.symbol)
    if args.book:
        from robinhood_tools.alpaca_paper import (
            AlpacaPaperBackend,
            AlpacaPaperHttpTransport,
        )
        from robinhood_tools.settings import load_env

        env = load_env(".env")
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        symbols = [str(p.get("symbol", "")) for p in backend.list_positions()
                   if len(str(p.get("symbol", ""))) > 10]
    if not symbols:
        print("no symbols given (--symbol or --book)")
        return 2

    results = []
    for symbol in symbols:
        try:
            quote, sigma, contract = fetch_quote(symbol,
                                                 max_age_minutes=max_age)
        except Exception as exc:  # noqa: BLE001 - report, never crash the run
            print(f"{symbol}: quote failed ({type(exc).__name__}: {exc})")
            results.append({"symbol": symbol, "error": str(exc)})
            continue
        if sigma is None:
            print(f"{symbol}: no vol estimate (realized and IV both missing); "
                  f"failing closed - no odds claimed")
            results.append({"symbol": symbol, "error": "no vol estimate"})
            continue
        premium0 = float(quote.mid())
        delta = float(quote.delta) if quote.delta is not None else 0.5
        days = args.days or min(max(contract.dte(date.today().isoformat()), 1),
                                BLACKOUT_DAYS)
        odds = simulate_option_odds(
            premium0=premium0, delta=delta, spot0=float(quote.underlying_price),
            sigma=sigma, days=days, paths=args.paths,
            target_pct=args.target_pct, stop_pct=args.stop_pct, seed=args.seed)
        row = {
            "symbol": symbol, "underlying": contract.underlying,
            "right": contract.right, "strike": str(contract.strike),
            "expiry": contract.expiry, "dte": contract.dte(date.today().isoformat()),
            "premium0": round(premium0, 2), "delta": round(delta, 4),
            "sigma_annual": round(sigma, 4), "horizon_days": days,
            "generated_at": datetime.now(ET).isoformat(),
            "stale_quote_allowed": bool(args.allow_stale), **odds,
        }
        results.append(row)
        print(json.dumps(row))

    payload = {"generated_at": datetime.now(ET).isoformat(),
               "model": "delta-only GBM; gamma/vega/theta ignored",
               "results": results}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"written -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())