#!/usr/bin/env python3
"""Trading state for the office site: positions, buys, weekly objective.

Assembles one fail-safe JSON from the desk's own records:
  - account equity + the weekly profit objective (progress vs $10,000)
  - open positions (symbol, qty, entry, current, unrealized P/L)
  - today's decisions (per-ticker auto.json: decision, action, reason)
  - options entries placed by the 15:10 loop (from its watch log)
  - the scorecard: graded observations and thesis right/wrong so far
  - recent shadow rows (what the desk was blocked from and why)

Read-only. Consumed by /api/trading on the office viewer.

Usage:
  python scripts/trading_state.py            # print the JSON
  python scripts/trading_state.py --watch    # refresh every 30s
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
AUTO_DIR = ROOT / "outputs" / "paper" / "tradingagents"
WATCH_LOG = ROOT / "outputs" / "options-watch.log"


def _objective(today: str) -> dict:
    goal_file = ROOT / "outputs" / "learning" / "profit_goal.json"
    equity = None
    try:
        from robinhood_tools.alpaca_paper import (AlpacaPaperBackend,
                                                  AlpacaPaperHttpTransport)
        from robinhood_tools.settings import load_env

        env = {**load_env(".env"), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        equity = float(backend.transport.request("GET", "/v2/account")["equity"])
    except Exception:  # noqa: BLE001 - the page renders without live equity
        pass
    if not goal_file.exists():
        return {"equity": equity}
    goal = json.loads(goal_file.read_text(encoding="utf-8"))
    out = {"equity": equity, "target_pnl_usd": goal.get("target_pnl_usd"),
           "cadence": goal.get("cadence")}
    if goal.get("cadence") == "weekly":
        monday = (datetime.now(ET).date() - timedelta(days=datetime.now(ET).weekday())).isoformat()
        baseline = goal.get("week_baseline_equity_usd")
        if goal.get("week_start") != monday or not baseline:
            baseline = equity  # new week: first render sets the baseline
        if baseline and equity:
            out.update({"week_of": goal.get("week_start", monday),
                        "week_pnl_usd": round(equity - float(baseline), 2),
                        "week_pct": round((equity - float(baseline))
                                          / float(goal["target_pnl_usd"]) * 100, 1),
                        "sessions_left": max(0, 4 - datetime.now(ET).weekday())})
        if goal.get("anchor_equity_usd") and equity:
            out["all_time_pnl_usd"] = round(
                equity - float(goal["anchor_equity_usd"]), 2)
    else:
        baseline = goal.get("baseline_equity_usd")
        if baseline and equity:
            out["pnl_usd"] = round(equity - float(baseline), 2)
    return out


def _positions() -> list[dict]:
    try:
        from robinhood_tools.alpaca_paper import (AlpacaPaperBackend,
                                                  AlpacaPaperHttpTransport)
        from robinhood_tools.settings import load_env

        env = {**load_env(".env"), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        out = []
        for p in backend.list_positions():
            out.append({k: p.get(k) for k in
                        ("symbol", "qty", "avg_entry_price", "current_price",
                         "market_value", "unrealized_pl", "unrealized_plpc")})
        return out
    except Exception:  # noqa: BLE001
        return []


def _today_records(today: str) -> list[dict]:
    records = []
    if AUTO_DIR.exists():
        for path in sorted(AUTO_DIR.glob(f"*/{today}/auto.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            records.append({k: rec.get(k) for k in
                            ("ticker", "decision", "action", "reason",
                             "desk_verdict", "zone", "trade_style",
                             "qty", "limit")})
    return records


def _option_entries(today: str) -> list[str]:
    entries = []
    if WATCH_LOG.exists():
        for line in WATCH_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            if "[entry] PLACED" in line:
                entries.append(line.split("PLACED", 1)[1].strip())
    return entries


def _scorecard() -> dict:
    try:
        from robinhood_tools.database import CioDatabase
        from robinhood_tools.runtime import build_settings

        db = CioDatabase(build_settings("config/approval_routes.json",
                                        ".env").database_path)
        with db.connect() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM strategy_observations "
                "WHERE actual_return IS NOT NULL")]
            graded = rows
            wins = sum(1 for r in graded if r.get("thesis_accurate") == 1)
            losses = sum(1 for r in graded if r.get("thesis_accurate") == 0)
            excess = [float(r["actual_return"]) - float(r["benchmark_return"])
                      for r in graded if r.get("benchmark_return")]
            shadows = conn.execute(
                "SELECT count(*) FROM shadow_equity_recommendations").fetchone()[0]
        return {"graded": len(graded), "thesis_right": wins,
                "thesis_wrong": losses, "shadow_rows": shadows,
                "avg_excess_pct": round(sum(excess) / len(excess), 2) if excess else None}
    except Exception:  # noqa: BLE001
        return {}


def _recent_shadows(limit: int = 8) -> list[dict]:
    try:
        from robinhood_tools.database import CioDatabase
        from robinhood_tools.runtime import build_settings

        db = CioDatabase(build_settings("config/approval_routes.json",
                                        ".env").database_path)
        out = []
        for r in db.shadow_recommendations(limit):
            payload = r.get("payload") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    payload = {}
            out.append({"symbol": r.get("symbol"), "action": r.get("action"),
                        "regime": r.get("market_regime"),
                        "decision": payload.get("decision", ""),
                        "reason": payload.get("reason", "")[:120],
                        "date": str(r.get("observed_at", ""))[:10]})
        return out
    except Exception:  # noqa: BLE001
        return []


def state() -> dict:
    today = datetime.now(ET).date().isoformat()
    return {
        "generated_at": datetime.now(ET).isoformat(),
        "date": today,
        "objective": _objective(today),
        "positions": _positions(),
        "today": _today_records(today),
        "option_entries": _option_entries(today),
        "scorecard": _scorecard(),
        "recent_shadows": _recent_shadows(),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Trading state for the office site.")
    p.add_argument("--watch", action="store_true", help="refresh every 30s")
    args = p.parse_args(argv)
    try:
        while True:
            print(json.dumps(state(), indent=2, default=str))
            if not args.watch:
                return 0
            import time

            time.sleep(30)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
