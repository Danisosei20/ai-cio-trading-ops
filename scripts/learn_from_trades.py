#!/usr/bin/env python3
"""Close the learning loop: settle due checkpoints into observations.

For each due, uncompleted learning checkpoint:
  entry (broker fills) vs now (broker marks) -> return, MFE/MAE from bars
  SPY over the same window -> benchmark + excess
  thesis accuracy: profitable in the traded direction
  error attribution: none | thesis | timing | execution
Writes strategy_observations, marks checkpoints complete, and appends
standing lessons to outputs/paper/lessons.json (read by the desk UI).

  python3 scripts/learn_from_trades.py --dry-run   # report only
  python3 scripts/learn_from_trades.py             # record
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.database import CioDatabase  # noqa: E402
from robinhood_tools.runtime import build_settings  # noqa: E402

LESSONS_FILE = Path("outputs/paper/lessons.json")

STANDING_LESSONS = [
    {"lesson": "Intraday trend-following fails out-of-sample with costs.",
     "evidence": "backtests 20261006: NVDA sma20/50 +139% IS -> -61% OOS",
     "rule": "No intraday trend strategy trades without incubation PASS."},
    {"lesson": "Overnight gaps carry the only validated edge so far.",
     "evidence": "overnight50 NVDA +155% IS -> +28% OOS, 182 OOS trades",
     "rule": "Overnight pilot only; small fixed notional until live confirms."},
    {"lesson": "NO_TRADE is a successful outcome; volume is not a metric.",
     "evidence": "2026-10-07 debates: 4 skips, 3 qualified buys, 0 forced trades",
     "rule": "Never lower a gate to manufacture activity."},
]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Settle due learning checkpoints.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    settings = build_settings(args.config, args.env_file)

    import os

    from robinhood_tools.alpaca_market_data import AlpacaMarketDataHttpClient
    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
    from robinhood_tools.settings import load_env

    env = {**load_env(args.env_file), **os.environ}
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    data = AlpacaMarketDataHttpClient.from_values(env)
    db = CioDatabase(settings.database_path)
    today = date.today().isoformat()

    try:
        spy_bars = data.stock_bars("SPY", timeframe="1D", start="2026-01-01", limit=1000)
        spy = [(b["t"][:10], Decimal(str(b["c"]))) for b in spy_bars]
    except Exception as exc:
        print(f"benchmark unavailable ({type(exc).__name__}); aborting.")
        return 2

    def spy_return(start: str, end: str) -> Decimal | None:
        lo = next((c for d, c in spy if d >= start), None)
        hi = next((c for d, c in reversed(spy) if d <= end), None)
        if lo is None or hi is None or lo <= 0:
            return None
        return hi / lo - 1

    due = db.due_learning_checkpoints(today)
    print(f"due checkpoints: {len(due)}")
    results = []
    for cp in due:
        rec = {"recommendation": cp["recommendation_id"], "day": cp["trading_day"]}
        try:
            approval = db.get(cp["recommendation_id"])
            fills = backend.get_fills(account_id=approval.account_id
                                      if hasattr(approval, "account_id") else "",
                                      order_id=approval.order_id or "")
        except Exception as exc:  # noqa: BLE001 - record and continue
            rec.update({"action": "skip", "reason": f"{type(exc).__name__}"})
            results.append(rec)
            print(json.dumps(rec))
            continue
        try:
            if not fills:
                rec.update({"action": "skip", "reason": "no fills yet"})
                results.append(rec)
                print(json.dumps(rec))
                continue
            qty = sum(Decimal(str(f.quantity)) for f in fills)
            cost = sum(Decimal(str(f.quantity)) * Decimal(str(f.price)) for f in fills)
            entry = cost / qty if qty else Decimal("0")
            symbol = approval.symbol
            marks = backend.list_positions()
            mark_row = next((p for p in marks if str(p.get("symbol", "")).upper() == symbol), None)
            if mark_row is None:
                rec.update({"action": "skip", "reason": "position closed; exit attribution pending"})
                if not args.dry_run:
                    db.record_strategy_observation(
                        recommendation_id=cp["recommendation_id"], symbol=symbol,
                        strategy_version="v1", market_regime="unknown",
                        horizon_days=cp["trading_day"], expected_return=None,
                        actual_return=None, benchmark_return=None,
                        max_favorable_excursion=None, max_adverse_excursion=None,
                        thesis_accurate=None, error_category="exit-unrecorded")
                    db.complete_learning_checkpoint(cp["recommendation_id"], cp["trading_day"])
                    rec["action"] = "drained-null"
                results.append(rec)
                print(json.dumps(rec))
                continue
            mark = Decimal(str(mark_row.get("current_price", "0")))
            ret = mark / entry - 1 if entry > 0 else Decimal("0")
            start = approval.created_at[:10]
            bench = spy_return(start, today)
            try:
                bars = data.stock_bars(symbol, timeframe="1D", start=start, limit=1000)
                highs = [Decimal(str(b["h"])) for b in bars]
                lows = [Decimal(str(b["l"])) for b in bars]
                mfe = (max(highs) / entry - 1) if highs and entry > 0 else None
                mae = (min(lows) / entry - 1) if lows and entry > 0 else None
            except Exception:
                mfe = mae = None
            accurate = ret > 0
            if not accurate:
                error = "thesis"
            elif mfe is not None and mfe - ret > Decimal("0.05"):
                error = "timing"
            else:
                error = "none"
            rec.update({"symbol": symbol, "return": f"{ret:.2%}",
                        "benchmark": f"{bench:.2%}" if bench is not None else None,
                        "accurate": accurate, "error": error,
                        "mfe": f"{mfe:.2%}" if mfe is not None else None,
                        "mae": f"{mae:.2%}" if mae is not None else None})
            if not args.dry_run:
                db.record_strategy_observation(
                    recommendation_id=cp["recommendation_id"], symbol=symbol,
                    strategy_version="v1", market_regime="unknown",
                    horizon_days=cp["trading_day"], expected_return=None,
                    actual_return=f"{ret:.4f}",
                    benchmark_return=f"{bench:.4f}" if bench is not None else None,
                    max_favorable_excursion=f"{mfe:.4f}" if mfe is not None else None,
                    max_adverse_excursion=f"{mae:.4f}" if mae is not None else None,
                    thesis_accurate=accurate, error_category=error)
                db.complete_learning_checkpoint(cp["recommendation_id"], cp["trading_day"])
                rec["action"] = "recorded"
            else:
                rec["action"] = "dry_run"
            results.append(rec)
            print(json.dumps(rec, default=str))
        except (InvalidOperation, KeyError, AttributeError) as exc:
            rec.update({"action": "skip", "reason": f"{type(exc).__name__}"})
            results.append(rec)
            print(json.dumps(rec))
    lessons = {"updated": today, "standing": STANDING_LESSONS, "settled": results}
    if not args.dry_run:
        LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        LESSONS_FILE.write_text(json.dumps(lessons, indent=2, default=str))
    print(f"settled={sum(1 for r in results if r.get('action') == 'recorded')}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
