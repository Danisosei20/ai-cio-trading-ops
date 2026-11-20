#!/usr/bin/env python3
"""Read-only metrics report for the paper trading desk.

This report distinguishes observed evidence from data the current ledger does
not retain. It never calls a broker, places orders, or mutates the source DB.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.performance_by_scenario import round_trips_with_dates  # noqa: E402
from robinhood_tools.desk_policy import POLICY  # noqa: E402

ET = ZoneInfo("America/New_York")
DEFAULT_DB = ROOT / "outputs" / "paper" / "cio.db"
DEFAULT_GUARD = ROOT / "outputs" / "paper" / "options_guard.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "paper" / "paper_session_report.json"
DEFAULT_MAX_IV = POLICY.quoted_iv_cap
DEFAULT_REWARD_PCT = POLICY.modeled_reward_pct
DEFAULT_STOP_PCT = POLICY.guard_stop_pct


def _payload(row: dict) -> dict:
    value = row.get("payload", {})
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _dates(rows: list[dict], key: str) -> set[str]:
    return {str(row.get(key, ""))[:10] for row in rows if str(row.get(key, ""))[:10]}


def _open_position_count(fills: list[dict]) -> int:
    net: defaultdict[str, Decimal] = defaultdict(Decimal)
    for row in fills:
        symbol = str(row.get("symbol", "")).strip().upper()
        try:
            qty = Decimal(str(row.get("quantity", "0")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if str(row.get("side", "")).lower() == "buy":
            net[symbol] += qty
        elif str(row.get("side", "")).lower() == "sell":
            net[symbol] -= qty
    return sum(1 for qty in net.values() if qty > 0)


def _reason_counts(candidates: list[dict], max_iv: float) -> dict[str, int]:
    counts = Counter()
    for row in candidates:
        payload = _payload(row)
        reason = str(payload.get("reason", payload.get("rejection_reason", ""))).lower()
        try:
            iv = float(payload["iv"]) if payload.get("iv") is not None else None
        except (TypeError, ValueError):
            iv = None
        source = str(payload.get("source", "")).lower()
        iv_observed = iv is not None and iv > max_iv and "option" in source
        explicit_iv_rejection = "iv" in reason and ("cap" in reason or "above" in reason or "exceed" in reason)
        if iv_observed:
            counts["iv_cap"] += 1
        if explicit_iv_rejection:
            counts["iv_cap_explicit"] += 1
        if "reward/risk" in reason or "2:1" in reason:
            counts["reward_risk"] += 1
        if "vix" in reason or "regime" in reason:
            counts["regime_vix"] += 1
    return {key: counts[key] for key in ("iv_cap", "iv_cap_explicit", "reward_risk", "regime_vix")}


def summarise_paper_sessions(
    fills: list[dict],
    candidates: list[dict],
    guard: dict,
    *,
    max_iv: float = DEFAULT_MAX_IV,
    modeled_reward_pct: float = DEFAULT_REWARD_PCT,
    stop_pct: float = DEFAULT_STOP_PCT,
) -> dict:
    trips = round_trips_with_dates(fills)
    pnls = [t["pnl"] for t in trips]
    wins = sum(1 for pnl in pnls if pnl > 0)
    fees = Decimal("0")
    for row in fills:
        try:
            fees += Decimal(str(row.get("fee", "0")))
        except (InvalidOperation, TypeError, ValueError):
            pass
    net = sum(pnls, Decimal("0")) - fees
    observed_days = _dates(fills, "filled_at") | _dates(candidates, "created_at")
    signals = Counter(str(row.get("signal", "UNKNOWN")) for row in guard.get("evaluated", []))
    block_counts = _reason_counts(candidates, max_iv)
    exit_counts = {name: signals.get(name, 0) for name in ("STOP", "TRAIL", "TARGET", "EXPIRE", "HOLD")}
    start = min(observed_days) if observed_days else None
    end = max(observed_days) if observed_days else None
    ledger_open = _open_position_count(fills)
    guard_open = len(guard.get("evaluated", []))
    return {
        "generated_at": datetime.now(ET).isoformat(),
        "window": {"start": start, "end": end, "session_days": len(observed_days)},
        "sessions": {"observed_days": len(observed_days), "target_min": 10, "target_max": 20,
                     "ready_for_policy_review": 10 <= len(observed_days) <= 20},
        "closed_round_trips": {"count": len(trips)},
        "open_positions": {"count": guard_open or ledger_open,
                           "ledger_unmatched_count": ledger_open,
                           "guard_evaluated": guard_open},
        "pnl": {"net_realized": round(float(net), 2), "gross_realized": round(float(sum(pnls, Decimal("0"))), 2),
                "fees": round(float(fees), 2), "wins": wins, "losses": len(pnls) - wins,
                "win_rate": round(wins / len(pnls), 3) if pnls else 0.0,
                "win_rate_basis": "gross_round_trip_pnl"},
        "blocks": {
            "iv_cap": {"observed": block_counts["iv_cap"],
                        "explicit_rejections": block_counts["iv_cap_explicit"],
                        "cap": max_iv},
            "reward_risk": {"observed": block_counts["reward_risk"], "minimum": 2.0,
                            "modeled_reward_pct": modeled_reward_pct, "stop_pct": stop_pct},
            "regime_vix": {"observed": block_counts["regime_vix"]},
        },
        "exits": {"current_guard_signals": exit_counts,
                  "historical_reason_status": "unavailable",
                  "historical_reason_note": "order_fills and trade_lifecycles do not retain exit cause"},
        "slippage": {"status": "unavailable",
                      "reason": "ledger has fills but no contemporaneous bid/ask or decision quote"},
        "evidence_gaps": ["historical exit reasons", "quoted slippage", "historical VIX/regime at every skip"],
    }


def _read_rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"select * from {table}")]
    except sqlite3.OperationalError:
        return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only paper-session metrics report.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--guard", type=Path, default=DEFAULT_GUARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output in {args.db.resolve(), args.guard.resolve()}:
        parser.error("--output must not overwrite --db or --guard")
    uri = f"file:{args.db.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        fills = _read_rows(conn, "order_fills")
        candidates = _read_rows(conn, "trade_candidates")
    conn.close()
    try:
        guard = json.loads(args.guard.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        guard = {"evaluated": []}
    report = summarise_paper_sessions(fills, candidates, guard)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"paper sessions: {report['sessions']['observed_days']} | "
          f"closed round-trips: {report['closed_round_trips']['count']} | "
          f"net realized: ${report['pnl']['net_realized']:,.2f}")
    print(f"written -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
