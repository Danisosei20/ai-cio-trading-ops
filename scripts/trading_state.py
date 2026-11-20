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


def _reconciliation() -> dict:
    """Latest fill-reconciliation run (outputs/paper/fill_reconciliation.json).

    The desk's ledger used to hold zero fills while the broker held every
    order, so no desk report could prove what had actually traded. The
    reconciler closes that gap each run; this surfaces its verdict -
    including orphan orders and exits it could not attribute - so the
    dashboard can never quietly disagree with the broker again.
    """
    path = ROOT / "outputs" / "paper" / "fill_reconciliation.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "never_run"}
    return {
        "status": doc.get("status", "unknown"),
        "generated_at": doc.get("generated_at", ""),
        "orders_at_broker": doc.get("orders_at_broker", 0),
        "fills_recorded": doc.get("fills_recorded", 0),
        "fills_already_known": doc.get("fills_already_known", 0),
        "lifecycles_closed": doc.get("lifecycles_closed", 0),
        "orphan_orders": doc.get("orphan_orders", []),
        "unattributed_exits": doc.get("unattributed_exits", []),
        "errors": doc.get("errors", []),
    }


def _position_odds(held_symbols: set[str] | None = None) -> dict:
    """Last options Time Machine run (outputs/simulation/...json), as-is.

    The simulation is expensive (thousands of paths plus market data), so
    the dashboard shows the last written run with its timestamp rather
    than re-simulating on every poll. The 09:45 batch refreshes it.

    Results are filtered to what is CURRENTLY held: after the 2026-11-17
    stop-loss filled, the dashboard kept quoting odds for a position that
    no longer existed - stale odds for a closed book are worse than none.
    """
    path = ROOT / "outputs" / "simulation" / "options_time_machine.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "never_run"}
    results = doc.get("results", [])
    if held_symbols is not None:
        results = [r for r in results
                   if str(r.get("symbol", "")).upper() in held_symbols]
    return {"status": "ok",
            "generated_at": doc.get("generated_at", ""),
            "model": doc.get("model", ""),
            "results": results}


def _live_regime() -> str:
    """Live tape regime: SPY vs its 50-day mean, never read as risk-on.

    The dashboard header showed "REGIME: —" because the payload carried no
    regime at all. This is the same helper the 15:10 watch gates on, so the
    header reads the same verdict the trading paths act on. An unreadable
    regime fails closed to UNKNOWN.
    """
    try:
        from robinhood_tools.market_prices import _spy_closes, market_regime

        return market_regime(_spy_closes())
    except Exception:  # noqa: BLE001 - an unreadable regime is never RISK_ON
        return "UNKNOWN"


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
        # Today's P/L vs previous close (Alpaca last_equity)
        try:
            acct = backend.transport.request("GET", "/v2/account")
            last_eq = float(acct.get("last_equity", 0))
            if last_eq:
                out["today_pnl_usd"] = round(equity - last_eq, 2)
        except Exception:  # noqa: BLE001
            pass
    else:
        baseline = goal.get("baseline_equity_usd")
        if baseline and equity:
            out["pnl_usd"] = round(equity - float(baseline), 2)
    return out


def _position_direction(symbol: str, qty: float) -> str:
    """Long/short by MARKET EXPOSURE, not by contract side.

    A bought put carries a positive qty (you own the contracts) but profits
    when the underlying falls. Classifying on qty alone reported four short
    puts as "Long - 4 / Short - 0", which reads as no downside exposure when
    the entire book is bearish - the operator asked for short exposure and the
    dashboard told them they had none.
    """
    if qty <= 0:
        return "short"
    if len(symbol) > 10:  # OCC contract, not an equity symbol
        try:
            from robinhood_tools.options import occ_details
            return "short" if occ_details(symbol).right == "put" else "long"
        except Exception:  # noqa: BLE001 - unparsable contract is not a short
            return "long"
    return "long"


def _positions() -> list[dict]:
    try:
        from robinhood_tools.alpaca_paper import (AlpacaPaperBackend,
                                                  AlpacaPaperHttpTransport)
        from robinhood_tools.settings import load_env

        env = {**load_env(".env"), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        out = []
        for p in backend.list_positions():
            qty = float(p.get("qty", 0))
            direction = _position_direction(str(p.get("symbol", "")), qty)
            pos = {k: p.get(k) for k in
                   ("symbol", "qty", "avg_entry_price", "current_price",
                    "market_value", "unrealized_pl", "unrealized_plpc")}
            pos["direction"] = direction
            out.append(pos)
        return out
    except Exception:  # noqa: BLE001
        return []


def _positions_by_direction() -> dict:
    """Split positions into long and short with aggregated P/L."""
    positions = _positions()
    long_positions = [p for p in positions if p.get("direction") == "long"]
    short_positions = [p for p in positions if p.get("direction") == "short"]
    
    long_pnl = sum(float(p.get("unrealized_pl", 0)) for p in long_positions)
    short_pnl = sum(float(p.get("unrealized_pl", 0)) for p in short_positions)
    
    return {
        "long": {
            "count": len(long_positions),
            "unrealized_pl": round(long_pnl, 2),
            "positions": long_positions,
        },
        "short": {
            "count": len(short_positions),
            "unrealized_pl": round(short_pnl, 2),
            "positions": short_positions,
        },
        "total_unrealized_pl": round(long_pnl + short_pnl, 2),
    }


def _open_orders() -> list[dict]:
    """Unfilled orders at the broker.

    Added because the desk had a live short it could not see: a limit order
    that has not filled is not a position, so it appeared in no panel, and
    the entries list showed it as placed. Two of them stacked before anyone
    noticed.
    """
    try:
        import os

        from robinhood_tools.alpaca_paper import (
            AlpacaPaperBackend, AlpacaPaperHttpTransport)
        from robinhood_tools.settings import load_env

        env = {**load_env(".env"), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        out = []
        for o in backend.list_open_orders():
            out.append({"symbol": str(o.get("symbol", "")),
                        "side": str(o.get("side", "")),
                        "qty": str(o.get("qty", "")),
                        "limit_price": str(o.get("limit_price", "")),
                        "status": str(o.get("status", "")),
                        "submitted_at": str(o.get("submitted_at", ""))})
        return out
    except Exception:  # noqa: BLE001 - an unreadable book is not an empty one
        return []


def _strategy_performance() -> dict:
    """Strategy performance from trade state machine."""
    try:
        from robinhood_tools.trade_state import TradeStateMachine
        sm = TradeStateMachine()
        summary = sm.summary()
        return summary
    except Exception:  # noqa: BLE001
        return {"total_trades": 0, "open": 0, "closed": 0,
                "states": {}, "total_realized_pnl": 0.0}


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
    positions = _positions()
    held = {str(p.get("symbol", "")).upper() for p in positions}
    return {
        "generated_at": datetime.now(ET).isoformat(),
        "date": today,
        "regime": _live_regime(),
        "objective": _objective(today),
        "positions": positions,
        "positions_by_direction": _positions_by_direction(),
        "today": _today_records(today),
        "option_entries": _option_entries(today),
        "open_orders": _open_orders(),
        "reconciliation": _reconciliation(),
        "position_odds": _position_odds(held),
        "scorecard": _scorecard(),
        "strategy_performance": _strategy_performance(),
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
