#!/usr/bin/env python3
"""Friday review: the week's evidence, posted to Slack without being asked.

Composed from the desk's own records for the current ET week (Mon-Fri):
  - weekly P/L vs the $10,000 objective (equity vs the week's baseline)
  - every decision made and every gate that blocked it
  - the scorecard: graded observations, thesis right/wrong, avg excess vs S&P

This is the check-in the owner approved: 10 sessions of evidence, then a
verdict on whether to turn the governed dials or fix the decision engine.
Runs Fridays 16:45 ET (after the 16:30 settle + grade so the week is scored).

Usage:
  python scripts/weekly_review.py            # post to Slack
  python scripts/weekly_review.py --print    # compose and print only
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robinhood_tools.database import CioDatabase  # noqa: E402
from robinhood_tools.runtime import build_settings  # noqa: E402

ET = ZoneInfo("America/New_York")
AUTO_DIR = ROOT / "outputs" / "paper" / "tradingagents"


def _week_bounds(now: datetime) -> tuple[str, str]:
    monday = now.date() - timedelta(days=now.weekday())
    return monday.isoformat(), (monday + timedelta(days=5)).isoformat()


def _records(monday: str, sunday: str) -> list[dict]:
    records = []
    if AUTO_DIR.exists():
        for path in sorted(AUTO_DIR.glob("*/20*/auto.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
                if monday <= rec.get("date", "") <= sunday:
                    records.append(rec)
            except (OSError, ValueError):
                continue
    return records


def _ledger(monday: str, sunday: str) -> tuple[list[dict], list[dict]]:
    settings = build_settings("config/approval_routes.json", ".env")
    db = CioDatabase(settings.database_path)
    with db.connect() as conn:
        shadows = [dict(r) for r in conn.execute(
            "SELECT * FROM shadow_equity_recommendations "
            "WHERE substr(observed_at,1,10) BETWEEN ? AND ?", (monday, sunday))]
        graded = [dict(r) for r in conn.execute(
            "SELECT * FROM strategy_observations "
            "WHERE substr(created_at,1,10) BETWEEN ? AND ?", (monday, sunday))]
    return shadows, graded


def _equity() -> float | None:
    try:
        import os

        from robinhood_tools.alpaca_paper import (AlpacaPaperBackend,
                                                  AlpacaPaperHttpTransport)
        from robinhood_tools.settings import load_env

        env = {**load_env(".env"), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        return float(backend.transport.request("GET", "/v2/account")["equity"])
    except Exception:  # noqa: BLE001
        return None


def compose() -> str:
    now = datetime.now(ET)
    monday, sunday = _week_bounds(now)
    records = _records(monday, sunday)
    shadows, graded = _ledger(monday, sunday)
    equity = _equity()
    goal_file = ROOT / "outputs" / "learning" / "profit_goal.json"
    goal = {}
    if goal_file.exists():
        try:
            goal = json.loads(goal_file.read_text(encoding="utf-8"))
        except ValueError:
            goal = {}
    baseline = goal.get("week_baseline_equity_usd")
    lines = [f"*Weekly review - {monday} to {now.date().isoformat()}*"]
    if equity is not None and baseline:
        pnl = equity - float(baseline)
        lines.append(f"objective: {pnl:+,.0f} of 10,000 USD ({pnl / 10000 * 100:+.1f}%) "
                     f"| equity {equity:,.0f}")
    placed = [r for r in records if str(r.get("action", "")).endswith("_placed")]
    blocked = [r for r in records if r.get("reason")]
    lines.append(f"decisions: {len(records)} | orders placed: {len(placed)} | "
                 f"gated out: {len(blocked)}")
    for r in records:
        verdict = f" (desk: {r['desk_verdict']})" if r.get("desk_verdict") else ""
        lines.append(f"- {r.get('ticker', '?')}: {r.get('decision', '-')} -> "
                     f"{r.get('action', 'skip')} {r.get('reason', '')[:90]}{verdict}")
    if graded:
        wins = sum(1 for r in graded if r.get("thesis_accurate") == 1)
        losses = sum(1 for r in graded if r.get("thesis_accurate") == 0)
        excess = [float(r["actual_return"]) - float(r["benchmark_return"])
                  for r in graded if r.get("actual_return") and r.get("benchmark_return")]
        avg = sum(excess) / len(excess) if excess else 0.0
        lines.append(f"scorecard: {len(graded)} graded | thesis right {wins} / "
                     f"wrong {losses} | avg excess vs S&P {avg:+.2f}%")
    else:
        lines.append("scorecard: nothing settled yet - windows still trading")
    lines.append(f"shadow rows this week: {len(shadows)}")
    lines.append("_paper only - research software, not financial advice_")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Friday weekly review post.")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="compose and print; never send")
    args = p.parse_args(argv)
    message = compose()
    if args.print_only:
        print(message)
        return 0
    from scripts.notify import send

    record = send(message)
    print(f"weekly_review: {record['status']} -> {record['channel_id']} "
          f"ts={record['message_ts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
