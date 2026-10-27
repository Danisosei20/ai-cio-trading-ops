#!/usr/bin/env python3
"""One Slack summary per trading day: what the 09:45 run actually did.

Reads today's per-ticker records (outputs/paper/tradingagents/<TICKER>/<DATE>/auto.json)
plus today's shadow rows, and posts ONE compact message: screened tickers,
decisions, gates that blocked, and orders placed. Runs at the end of the
09:45 batch (weekdays, so the weekend mute never applies). Notification-only.

Usage:
  python scripts/run_summary.py            # post to Slack
  python scripts/run_summary.py --print    # compose and print, never send
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robinhood_tools.database import CioDatabase  # noqa: E402
from robinhood_tools.runtime import build_settings  # noqa: E402

ET = ZoneInfo("America/New_York")
AUTO_DIR = ROOT / "outputs" / "paper" / "tradingagents"


def collect(today: str) -> tuple[list[dict], list[dict]]:
    records = []
    if AUTO_DIR.exists():
        for path in sorted(AUTO_DIR.glob(f"*/{today}/auto.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    settings = build_settings("config/approval_routes.json", ".env")
    db = CioDatabase(settings.database_path)
    with db.connect() as conn:
        shadows = [dict(r) for r in conn.execute(
            "SELECT * FROM shadow_equity_recommendations WHERE observed_at LIKE ?",
            (f"{today}%",))]
    return records, shadows


def _goal_line() -> str:
    """Recurring weekly profit objective (fail-safe: silent on any error)."""
    try:
        import os
        from datetime import date, datetime, timedelta
        from zoneinfo import ZoneInfo

        from robinhood_tools.alpaca_paper import (AlpacaPaperBackend,
                                                  AlpacaPaperHttpTransport)
        from robinhood_tools.settings import load_env

        goal_file = ROOT / "outputs" / "learning" / "profit_goal.json"
        if not goal_file.exists():
            return ""
        goal = json.loads(goal_file.read_text(encoding="utf-8"))
        target = float(goal["target_pnl_usd"])

        env = {**load_env(".env"), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        equity = float(backend.transport.request("GET", "/v2/account")["equity"])

        if goal.get("cadence") != "weekly":
            baseline = float(goal["baseline_equity_usd"])
            pnl = equity - baseline
            return (f"*profit objective*: {pnl:+,.0f} USD of {target:,.0f} "
                    f"({pnl / target * 100:+.1f}%) | equity {equity:,.0f}")

        et = ZoneInfo("America/New_York")
        now = datetime.now(et)
        this_monday = now.date() - timedelta(days=now.weekday())
        baseline = goal.get("week_baseline_equity_usd")
        if goal.get("week_start") != this_monday.isoformat() or not baseline:
            # New trading week: refresh the anchor and capture the baseline
            # equity from the first run of the week.
            goal["week_start"] = this_monday.isoformat()
            goal["week_baseline_equity_usd"] = equity
            if not goal.get("anchor_equity_usd"):
                goal["anchor_equity_usd"] = equity
                goal["anchor_date"] = this_monday.isoformat()
                goal["target_pct_of_equity_week"] = round(target / equity * 100, 2)
            goal_file.write_text(json.dumps(goal, indent=2), encoding="utf-8")
            baseline = equity
        baseline = float(baseline)
        week_pnl = equity - baseline
        sessions_left = max(0, 4 - now.weekday())
        pace = (target - week_pnl) / sessions_left if sessions_left else float("nan")
        total = ""
        if goal.get("anchor_equity_usd"):
            total = (f" | all-time {equity - float(goal['anchor_equity_usd']):+,.0f} "
                     f"since {goal.get('anchor_date', '?')}")
        return (f"*weekly objective*: {week_pnl:+,.0f} of {target:,.0f} USD "
                f"({week_pnl / target * 100:+.1f}%) week of {goal['week_start']} | "
                f"equity {equity:,.0f}{total} | "
                f"{sessions_left} sessions left -> {pace:,.0f}/session pace")
    except Exception:  # noqa: BLE001 - the summary must never fail on the goal line
        return ""


def compose(today: str) -> str:
    records, shadows = collect(today)
    if not records and not shadows:
        return ""
    lines = [f"*AI CIO desk - {today} run summary*"]
    for rec in records:
        ticker = rec.get("ticker", "?")
        action = rec.get("action", "skip")
        decision = rec.get("decision", "-")
        if action.endswith("_placed"):
            lines.append(f"- *{ticker}*: ORDER {action} ({rec.get('qty', '?')} "
                         f"@ {rec.get('limit', '?')}, decision {decision})")
        else:
            reason = rec.get("reason", "") or "no actionable decision"
            lines.append(f"- {ticker}: {action} - {reason[:140]} (decision {decision})")
    for row in shadows:
        payload = row.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {}
        lines.append(f"- shadow: {row.get('symbol') or 'none'} "
                     f"[{row.get('action')}] regime={row.get('market_regime')}"
                     f" ({str(payload.get('reason', ''))[:100]})")
    placed = sum(1 for r in records if str(r.get("action", "")).endswith("_placed"))
    lines.append(f"_orders placed: {placed} | shadow rows: {len(shadows)}_")
    goal = _goal_line()
    if goal:
        lines.insert(1, goal)
    lines.append("_paper only - research software, not financial advice_")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Slack run summary for the 09:45 auto-trade run.")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="compose and print; never send")
    args = p.parse_args(argv)

    today = datetime.now(ET).date().isoformat()
    message = compose(today)
    if not message:
        print("run_summary: nothing recorded today; no summary posted")
        return 0
    if args.print_only:
        print(message)
        return 0
    from scripts.notify import send

    record = send(message)
    print(f"run_summary: {record['status']} -> {record['channel_id']} "
          f"ts={record['message_ts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
