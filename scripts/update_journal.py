#!/usr/bin/env python3
"""Append a portable, non-secret observation to the strategy learning journal."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path


FIELDS = (
    "recorded_at", "symbol", "relative_volume", "outcome_20d",
    "benchmark_20d", "excess_return_20d",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Append an AI-CIO learning observation.")
    parser.add_argument("journal", type=Path)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--relative_volume", default="")
    parser.add_argument("--outcome_20d", default="")
    parser.add_argument("--benchmark_20d", default="")
    parser.add_argument("--excess_return_20d", default="")
    args = parser.parse_args()

    args.journal.parent.mkdir(parents=True, exist_ok=True)
    new_file = not args.journal.exists() or args.journal.stat().st_size == 0
    row = {field: getattr(args, field, "") for field in FIELDS}
    row["recorded_at"] = datetime.now(timezone.utc).isoformat()
    with args.journal.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
