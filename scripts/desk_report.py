#!/usr/bin/env python3
"""Desk performance report: metrics + confidence + champion/challenger.

Combines desk_metrics.py and confidence.py into a single report.
Runs as part of the weekly review (Friday 16:45) or on demand.

Usage:
  python scripts/desk_report.py              # full report
  python scripts/desk_report.py --json        # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.desk_metrics import compute_metrics, load_trades
from robinhood_tools.confidence import SetupScorer


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Desk performance report.")
    p.add_argument("--days", type=int, default=30, help="lookback days")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)

    trades = load_trades(args.days)
    metrics = compute_metrics(trades)
    scorer = SetupScorer()

    if args.json:
        report = {
            "metrics": metrics,
            "confidence": scorer.champion_challenger_status(),
            "strategy_report": scorer.strategy_report(),
        }
        print(json.dumps(report, indent=2))
    else:
        print("=== DESK PERFORMANCE REPORT ===")
        print(f"Period: last {args.days} days")
        print()
        print("--- Risk-Adjusted Metrics ---")
        print(f"  Trades:         {metrics.get('total_trades', 0)}")
        print(f"  Win rate:       {metrics.get('win_rate', 0):.1%}")
        print(f"  Total P/L:      ${metrics.get('total_pnl', 0):,.2f}")
        print(f"  Profit factor:  {metrics.get('profit_factor', 0)}")
        print(f"  Expectancy:     ${metrics.get('expectancy', 0):,.2f}/trade")
        print(f"  Max drawdown:   {metrics.get('max_drawdown_pct', 0):.1f}%")
        print(f"  Sharpe:         {metrics.get('sharpe', 0):.2f}")
        print(f"  Sortino:        {metrics.get('sortino', 0)}")
        print()
        print("  By strategy:")
        for strat, data in metrics.get("by_strategy", {}).items():
            print(f"    {strat}: {data['trades']} trades, {data['wins']} wins "
                  f"({data['win_rate']:.0%}), P/L=${data['pnl']:,.2f}")
        print()
        print("--- Confidence Calibration ---")
        print(scorer.strategy_report())
        print()
        print("--- Champion/Challenger Status ---")
        for strat, info in scorer.champion_challenger_status().items():
            status = "PROMOTED" if info["promoted"] else "shadow"
            print(f"  {strat}: {info['trades']} trades, win_rate={info['win_rate']:.0%}, "
                  f"expectancy={info['expectancy']:+.2f} -> {status} ({info['reason']})")
        print()
        print("NOTE: $10,000 weekly target is aspirational dashboard reference only.")
        print("Primary metrics: net returns, max drawdown, profit factor,")
        print("expectancy, Sharpe/Sortino, and strategy-specific performance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
