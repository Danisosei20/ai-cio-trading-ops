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
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
OUT = ROOT / "outputs" / "simulation" / "options_time_machine.json"
BLACKOUT_DAYS = 10  # horizon: a position older than this is not "now"

# The model lives in the package so the entry gate, this CLI and the tests
# all run the same math (re-exported here for backwards compatibility).
from robinhood_tools.options_gate import DEFAULT_MIN_EV_PCT  # noqa: E402
from robinhood_tools.options_odds import (  # noqa: E402
    annualized_vol,
    simulate_option_odds,
)


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


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Options-book Time Machine.")
    ap.add_argument("--symbol", action="append", default=[],
                    help="OCC symbol; repeatable. --book reads the broker.")
    ap.add_argument("--book", action="store_true",
                    help="simulate every open long option position")
    ap.add_argument("--paths", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--target-pct", type=float, default=0.0,
                    help="take-profit band (default 0 = disabled, winners run; "
                         "mirrors the guard's policy)")
    ap.add_argument("--stop-pct", type=float, default=0.15)
    ap.add_argument("--arm-pct", type=float, default=0.25,
                    help="trail arm: peak return that locks the floor")
    ap.add_argument("--floor-ratio", type=float, default=0.40,
                    help="trail floor as a share of the peak premium")
    ap.add_argument("--days", type=int, default=None,
                    help="horizon in trading days (default: min(DTE, 10)")
    ap.add_argument("--allow-stale", action="store_true",
                    help="simulate off the last quote even when the market is "
                         "closed (the trading path still fails closed on "
                         "stale quotes; a simulation is not a decision)")
    ap.add_argument("--candidates", action="store_true",
                   help="dry-run the ENTRY odds gate over the current "
                        "screener feeds (what the watch would face)")
    ap.add_argument("--min-ev-pct", type=float, default=DEFAULT_MIN_EV_PCT,
                    help="mirrors the live entry gate's margin so this "
                         "report shows what the 15:10 watch would actually "
                         "face (owner set +10% on 2026-11-17)")
    return ap.parse_args(argv)


def run_candidates(args) -> int:
    """Dry-run the entry odds gate across the screener's candidates.

    Evidence, not a decision: each row is what the 15:10 watch would see
    tomorrow for that contract under the current exit policy. Writes
    outputs/simulation/options_gate_dryrun.json.
    """
    import json as _json

    from robinhood_tools.alpaca_options import AlpacaOptionsData
    from robinhood_tools.market_prices import get_quote
    from robinhood_tools.options_flow import pick_long_option
    from robinhood_tools.options_gate import (
        entry_odds_verdict,
        sigma_for_underlier,
    )
    from robinhood_tools.settings import load_env

    env = load_env(".env")
    data = AlpacaOptionsData.from_values(env)
    today = date.today()
    rows = []
    for feed, side in (("outputs/screener/latest.json", "call"),
                       ("outputs/screener/weak.json", "put")):
        path = ROOT / feed
        if not path.exists():
            continue
        doc = _json.loads(path.read_text(encoding="utf-8"))
        for row in (doc.get("ranked") or [])[:5]:
            symbol = str(row.get("symbol") or "").upper().replace(".", "-")
            if not symbol:
                continue
            try:
                spot_q = get_quote(symbol, max_age_minutes=1440)
                quote, _rejected = pick_long_option(
                    data=data, underlying=symbol, spot=spot_q.price,
                    direction="bullish" if side == "call" else "bearish",
                    today=today)
            except Exception as exc:  # noqa: BLE001 - no quote is an answer
                rows.append({"symbol": symbol, "side": side,
                             "error": f"{type(exc).__name__}: {exc}"})
                continue
            iv = float(quote.iv) if quote.iv else None
            sigma = sigma_for_underlier(symbol, iv)
            if sigma is None:
                rows.append({"symbol": symbol, "side": side,
                             "contract": quote.contract.option_symbol,
                             "approved": False,
                             "reason": "no vol estimate (fail closed)"})
                continue
            verdict = entry_odds_verdict(
                premium0=float(quote.mid()),
                delta=float(quote.delta) if quote.delta is not None else 0.5,
                spot0=float(quote.underlying_price), sigma=sigma,
                expiry=quote.contract.expiry, today=today,
                paths=args.paths, min_ev_pct=args.min_ev_pct)
            verdict.update({"symbol": symbol, "side": side,
                            "contract": quote.contract.option_symbol,
                            "premium": round(float(quote.mid()), 2),
                            "delta": round(float(quote.delta or 0), 4),
                            "sigma": round(sigma, 4)})
            rows.append(verdict)
            print(_json.dumps(verdict))

    payload = {"generated_at": datetime.now(ET).isoformat(),
               "min_ev_pct": args.min_ev_pct, "candidates": rows}
    out = ROOT / "outputs" / "simulation" / "options_gate_dryrun.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
    print(f"written -> {out}")
    approved = sum(1 for r in rows if r.get("approved"))
    print(f"dry-run: {approved}/{len(rows)} candidates approved "
          f"at the {args.min_ev_pct:+.2%} bar")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    max_age = 1440 if args.allow_stale else 5

    if args.candidates:
        return run_candidates(args)

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
            target_pct=args.target_pct, stop_pct=args.stop_pct, seed=args.seed,
            arm_pct=args.arm_pct, floor_ratio=args.floor_ratio)
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