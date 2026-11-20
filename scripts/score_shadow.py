#!/usr/bin/env python3
"""Score the desk's shadow track record: 1/5/20 trading days vs the S&P 500.

Two idempotent steps (safe to run daily; scheduled weekdays 16:30 ET):

  1. SCHEDULE - every shadow_equity_recommendations row with a symbol that
     has NO learning checkpoints yet gets 1/5/20-trading-day due dates.
     (schedule_learning uses INSERT OR REPLACE, so re-scheduling would reset
     completed rows - hence the "never scheduled before" guard.)
  2. GRADE - due checkpoints are graded with realized closes (symbol vs
     ^GSPC), written to strategy_observations, then completed.

This is what turns the 09:45 run's blocked decisions into the actual answer
to "is the desk any good": excess return vs the benchmark, per idea, per
horizon. Prices from yfinance (a desk dependency already). Never trades.
Business-day math ignores exchange holidays (grading tolerance, documented).

Usage:
  ../TradingAgents/.venv/bin/python scripts/score_shadow.py     # schedule + grade
  python scripts/score_shadow.py --summary                      # scorecard so far
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robinhood_tools.database import CioDatabase  # noqa: E402
from robinhood_tools.runtime import build_settings  # noqa: E402

ET = ZoneInfo("America/New_York")
HORIZONS = (1, 5, 20)
STRATEGY_VERSION = "shadow-auto-v1"
BENCHMARK = "^GSPC"


def _iso(day) -> str:
    """Normalize a pandas index entry (Timestamp/datetime/str) to ISO date."""
    try:
        return day.date().isoformat()
    except AttributeError:
        return str(day)[:10]


def _closes(symbol: str, start: date, end: date) -> dict[str, float]:
    """Trading-date -> close from yfinance. Empty dict on any failure."""
    try:
        import yfinance as yf

        hist = yf.Ticker(symbol).history(start=start.isoformat(),
                                         end=end.isoformat(),
                                         auto_adjust=True)
        if "Close" not in hist:
            return {}
        return {_iso(d): float(v) for d, v in hist["Close"].dropna().items()}
    except Exception as exc:  # noqa: BLE001 - grading never crashes the chain
        print(f"score_shadow: price fetch failed for {symbol} "
              f"({type(exc).__name__}: {exc})")
        return {}


def _on_or_after(closes: dict[str, float], day: str) -> float | None:
    for key in sorted(closes):
        if key >= day:
            return closes[key]
    return None


def _window_extremes(closes: dict[str, float], lo: str, hi: str):
    window = [v for k, v in sorted(closes.items()) if lo <= k <= hi]
    if not window:
        return None, None
    return max(window), min(window)


def _due_dates(observed: date, horizon: int) -> str:
    try:
        import numpy as np

        target = np.busday_offset(observed.isoformat(), horizon, roll="forward")
        return date.fromisoformat(str(target)).isoformat()
    except Exception:  # noqa: BLE001 - calendar-day fallback, still unambiguous
        from datetime import timedelta

        return (observed + timedelta(days=horizon)).isoformat()


def _scheduled_rids(db: CioDatabase) -> set[str]:
    with db.connect() as conn:
        return {row["recommendation_id"] for row in conn.execute(
            "SELECT DISTINCT recommendation_id FROM learning_checkpoints")}


def schedule(db: CioDatabase) -> int:
    already = _scheduled_rids(db)
    created = 0
    for row in db.shadow_recommendations(500):
        rid = row["recommendation_id"]
        if rid in already or not row.get("symbol"):
            continue  # never-scheduled only; no_action rows have no symbol
        try:
            observed = datetime.fromisoformat(row["observed_at"]).date()
        except (TypeError, ValueError):
            continue
        try:
            db.schedule_learning(rid, {h: _due_dates(observed, h) for h in HORIZONS})
            created += 1
        except Exception as exc:  # noqa: BLE001
            print(f"score_shadow: schedule failed for {rid} ({type(exc).__name__})")
    print(f"score_shadow: scheduled {created} row(s) for 1/5/20-day grading")
    return created


def grade(db: CioDatabase, today: str) -> int:
    shadows = {row["recommendation_id"]: row
               for row in db.shadow_recommendations(500)}
    graded = 0
    for cp in db.due_learning_checkpoints(today):
        rid = cp["recommendation_id"]
        shadow = shadows.get(rid)
        if not shadow or not shadow.get("symbol"):
            db.complete_learning_checkpoint(rid, cp["trading_day"])
            continue
        symbol = shadow["symbol"]
        observed = shadow["observed_at"][:10]
        due = cp["due_date"]
        # yfinance end is exclusive: pad the window so the due date itself
        # (and any holiday gap) is covered; _on_or_after picks the right row.
        window_end = date.fromisoformat(due) + timedelta(days=4)
        sym_px = _closes(symbol, date.fromisoformat(observed), window_end)
        bench_px = _closes(BENCHMARK, date.fromisoformat(observed), window_end)
        base = _on_or_after(sym_px, observed)
        px_now = _on_or_after(sym_px, due)
        bm_0 = _on_or_after(bench_px, observed)
        bm_1 = _on_or_after(bench_px, due)
        if base is None or px_now is None or bm_0 is None or bm_1 is None:
            stale = date.fromisoformat(due) <= date.today() - timedelta(days=3)
            if stale:
                # Prices for a due date this old mean the symbol itself has no
                # data (bad ticker): close the checkpoint out honestly.
                try:
                    db.record_strategy_observation(
                        recommendation_id=rid, symbol=symbol,
                        strategy_version=STRATEGY_VERSION,
                        market_regime=shadow.get("market_regime", "unknown"),
                        horizon_days=int(cp["trading_day"]),
                        expected_return=None, actual_return=None,
                        benchmark_return=None, max_favorable_excursion=None,
                        max_adverse_excursion=None, thesis_accurate=None,
                        error_category="price_unavailable")
                    db.complete_learning_checkpoint(rid, cp["trading_day"])
                    print(f"score_shadow: {symbol} h{cp['trading_day']} - "
                          "no price data; closed as price_unavailable")
                except Exception as exc:  # noqa: BLE001
                    print(f"score_shadow: {rid} close-out failed ({type(exc).__name__})")
                continue
            print(f"score_shadow: {symbol} h{cp['trading_day']} - closes "
                  "unavailable, leaving checkpoint open")
            continue
        actual = (px_now - base) / base * 100
        benchmark_ret = (bm_1 - bm_0) / bm_0 * 100
        mfe, mae = _window_extremes(sym_px, observed, due)
        action = shadow.get("action", "")
        try:
            db.record_strategy_observation(
                recommendation_id=rid, symbol=symbol,
                strategy_version=STRATEGY_VERSION,
                market_regime=shadow.get("market_regime", "unknown"),
                horizon_days=int(cp["trading_day"]),
                expected_return=None,
                actual_return=str(round(actual, 4)),
                benchmark_return=str(round(benchmark_ret, 4)),
                max_favorable_excursion=None if mfe is None else str(round((mfe - base) / base * 100, 4)),
                max_adverse_excursion=None if mae is None else str(round((mae - base) / base * 100, 4)),
                thesis_accurate=None if action != "paper_candidate" else actual > 0,
                error_category=None if action == "paper_candidate" else action,
            )
            db.complete_learning_checkpoint(rid, cp["trading_day"])
            graded += 1
            print(f"score_shadow: {symbol} h{cp['trading_day']} graded - "
                  f"actual {actual:+.2f}% vs benchmark {benchmark_ret:+.2f}%")
        except Exception as exc:  # noqa: BLE001
            print(f"score_shadow: {rid} h{cp['trading_day']} grade failed "
                  f"({type(exc).__name__})")
    print(f"score_shadow: graded {graded} checkpoint(s)")
    return graded


def summary(db: CioDatabase) -> None:
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM strategy_observations ORDER BY created_at DESC")]
    graded = [r for r in rows if r["actual_return"] is not None]
    if not graded:
        print("score_shadow: no graded observations yet - "
              "shadow rows need 1+ trading days before they can score")
        return
    wins = sum(1 for r in graded if r["thesis_accurate"] == 1)
    losses = sum(1 for r in graded if r["thesis_accurate"] == 0)
    excess = [float(r["actual_return"]) - float(r["benchmark_return"])
              for r in graded if r["benchmark_return"] is not None]
    print(f"score_shadow: {len(graded)} graded observation(s)")
    print(f"  thesis right: {wins} | wrong: {losses} | "
          f"no-thesis rows: {len(graded) - wins - losses}")
    if excess:
        print(f"  avg excess vs S&P 500: {sum(excess) / len(excess):+.2f}% "
              f"across {len(excess)} horizon(s)")
    for r in rows[:8]:
        print(f"  {r['symbol']:6} h{r['horizon_days']:<3} "
              f"actual {float(r['actual_return'] or 0):+7.2f}%  "
              f"benchmark {float(r['benchmark_return'] or 0):+7.2f}%  "
              f"regime {r['market_regime']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Grade the desk's shadow track record.")
    p.add_argument("--summary", action="store_true", help="print the scorecard and exit")
    args = p.parse_args(argv)

    settings = build_settings("config/approval_routes.json", ".env")
    db = CioDatabase(settings.database_path)
    if args.summary:
        summary(db)
        return 0
    schedule(db)
    grade(db, datetime.now(ET).date().isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
