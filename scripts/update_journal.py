#!/usr/bin/env python3
"""Append one schema-validated row to the portable AI-CIO CSV journal."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path


FIELDS = [
    "date", "time_et", "account", "action", "symbol", "asset_class", "order_type",
    "amount_usd", "quantity", "status", "order_id", "thesis", "recommendation", "score",
    "probability", "reward_risk", "current_price", "intended_price", "avg_volume_20d",
    "avg_volume_50d", "relative_volume", "avg_daily_dollar_volume", "bid_ask_spread_pct",
    "order_pct_avg_volume", "return_20d", "return_60d", "relative_strength_sp500",
    "realized_volatility_20d", "atr_14d", "max_drawdown", "next_earnings_date",
    "signal_summary", "invalidation_level", "target_or_review_condition", "research_sources",
    "approval", "benchmark", "outcome", "lesson", "outcome_1d", "outcome_5d", "outcome_20d",
    "benchmark_20d", "excess_return_20d", "thesis_accuracy", "execution_slippage", "notes",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Append one AI-CIO learning-journal row.")
    parser.add_argument("journal", type=Path)
    for field in FIELDS:
        parser.add_argument(f"--{field}", default="")
    args = parser.parse_args()
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    exists = args.journal.exists() and args.journal.stat().st_size > 0
    if exists:
        with args.journal.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        if header != FIELDS:
            raise SystemExit("Journal schema mismatch; migrate the CSV before appending.")
    with args.journal.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({field: getattr(args, field) for field in FIELDS})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
