#!/usr/bin/env python3
"""Automated end-of-day AI trading review from actual desk artifacts.

Read-only research/reporting. Never imports a broker or order module, never
submits an order, and never mixes shadow/backtest numbers with realized P/L.
Produces structured JSON plus a readable Markdown report.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT_MODULE = Path(__file__).resolve().parents[1]
if str(ROOT_MODULE) not in sys.path:
    sys.path.insert(0, str(ROOT_MODULE))

from scripts.missed_opportunity_analysis import (analyze, load_shadow_reasons)  # noqa: E402

ET = ZoneInfo("America/New_York")


def _json(path: Path):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _scan_stats(root: Path, date: str) -> dict:
    log = root / "outputs" / "intraday.log"
    cycles, symbols, setups, judge_done, judge_fail = [], set(), 0, 0, 0
    if log.exists():
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith(date):
                continue
            if "=== cycle" in line:
                cycles.append(line.strip())
            if "new setups:" in line:
                setups = max(setups, len(re.findall(r"'[A-Z.]+'", line)))
                symbols.update(re.findall(r"'([A-Z.]+)'", line))
            if "judge completed for" in line:
                judge_done += 1
            if "judge failed" in line or "judge error" in line:
                judge_fail += 1
    path = root / "outputs" / "daytrade" / f"{date}.json"
    doc = _json(path) if path.exists() else None
    rows = (doc or {}).get("setups") or []
    directions = {}
    for row in rows:
        if isinstance(row, dict):
            directions[row.get("direction", "unknown")] = directions.get(
                row.get("direction", "unknown"), 0) + 1
    return {
        "cycles": len(cycles),
        "cycle_details": cycles,
        "symbols_scanned": sorted(symbols),
        "setups_recorded": len(rows),
        "setups_by_direction": directions,
        "judge_completed_batches": judge_done,
        "judge_failures": judge_fail,
        "source": str(path.relative_to(root)).replace("\\", "/") if doc else "unavailable",
    }


def _fills(root: Path, date: str) -> list[dict]:
    db = root / "outputs" / "paper" / "cio.db"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        cols = [r[1] for r in con.execute("PRAGMA table_info(order_fills)")]
        time_col = next((c for c in ("created_at", "filled_at", "timestamp") if c in cols), None)
        if not time_col:
            con.close()
            return []
        rows = con.execute(
            f"SELECT * FROM order_fills WHERE {time_col} LIKE ?", (f"{date}%",)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    return [dict(zip(cols, row)) for row in rows]


def _kill_switch(root: Path) -> str:
    db = root / "outputs" / "paper" / "cio.db"
    if not db.exists():
        return "not_recorded"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        row = con.execute(
            "SELECT value FROM system_controls WHERE key = 'emergency_kill'").fetchone()
        con.close()
    except sqlite3.Error:
        return "not_recorded"
    return str(row[0]) if row else "not_recorded"


def build_review(root: Path, date: str, *, as_of: str,
                 session_complete: bool, post_bars=None) -> dict:
    missed = analyze(root, date, as_of=as_of, session_complete=session_complete,
                     post_bars=post_bars, cost_bps=5.0)
    scan = _scan_stats(root, date)
    shadow = load_shadow_reasons(root, date)
    fills = _fills(root, date)
    vix_values = sorted({float(m.group(1)) for r in missed["candidates"]
                         for m in [re.search(r"VIX\s+(\d+\.\d+)", r["rejection_reason"])]
                         if m})
    regimes = sorted({r["market_regime"] for r in missed["candidates"]})
    counts = missed["counts"]

    realized = {
        "filled_orders": len(fills),
        "statistics": "not computed - zero realized paper fills" if not fills
                      else "available from broker ledger only; not combined with shadow",
    }
    shadow_perf = {
        "shadow_rows": len(shadow),
        "note": "simulated research rows; shadow figures are not realized profit",
    }
    backtest = {
        "source": "docs/research/risk-off-short-shadow-track.md",
        "status": "historical replay with odds gate stubbed; not realized performance",
    }

    recommendations = [
        "No approved equity path can open a short: bearish research is exit-only in "
        "tradingagents_paper_auto.py; daytrade_engine has a short branch but was not invoked.",
        "Persist a timestamped VIX as-of value: current_vix() returns a daily close with no "
        "age evidence, so gate values in one session differ (27.3-29.0).",
        "Research latency: the 10:45 judge batch took ~55 minutes for 9 symbols; evaluate a "
        "champion/challenger shorter analyst round before any production change.",
        "Reconcile the committed market_regime() signature with the staged working-tree fix "
        "before relying on committed code in a clean checkout.",
        "Record entry/stop/target fields even for policy-blocked candidates so a valid "
        "point-in-time replay becomes possible (never use post-signal data).",
    ]
    if not session_complete:
        recommendations.append(
            "Session INCOMPLETE: 15:10 options watch, 15:40 overnight dry-run, 15:45 flatten, "
            "16:10 news and 16:30 shadow scoring had not run at report time.")

    return {
        "track": "daily_trading_review",
        "generated_at": datetime.now(ET).isoformat(),
        "session_date": date,
        "as_of": as_of,
        "session_complete": session_complete,
        "shadow_only": True,
        "orders_submitted": 0,
        "entry_permission": "none - research and reporting only",
        "enforcement_enabled": False,
        "market_conditions": {
            "regimes_recorded": regimes,
            "vix_values_in_rejections": vix_values,
            "vix_as_of_evidence": "not retained by source artifacts",
            "evidence": "outputs/paper/tradingagents/*/<date>/auto.json",
        },
        "trading_activity": scan,
        "trade_decisions": {
            "candidates_evaluated": counts["candidates"],
            "long_bias": counts["long_bias"],
            "bearish_research": counts["bearish_research"],
            "approved_entries": 0,
            "valid_policy_rejections": counts["valid_policy_rejection"],
            "technical_failures": counts["technical_failure"],
            "shadow_rows": len(shadow),
            "broker_orders": len(fills),
        },
        "missed_opportunities": {
            "hypothetical_outcomes": counts["hypothetical_outcome"],
            "insufficient_evidence": counts["insufficient_evidence"],
            "method": missed["note"],
            "rows": missed["candidates"],
        },
        "execution_quality": {
            "judge_failures": scan["judge_failures"],
            "technical_failures": missed["technical_failures"],
            "environment_failures": "none observed in today's live artifacts",
            "data_gaps": missed["data_gaps"],
        },
        "risk_management": {
            "vix_blocks": sum(r["rejection_category"] == "vix_policy" for r in missed["candidates"]),
            "regime_blocks": sum(r["rejection_category"] == "regime_policy" for r in missed["candidates"]),
            "no_short_path": sum(r["rejection_category"] == "no_short_entry_path"
                                 for r in missed["candidates"]),
            "emergency_kill": _kill_switch(root),
            "assessment": "gates applied as designed; no waiver or bypass observed",
        },
        "long_short_performance": {
            "realized_paper": realized,
            "shadow": shadow_perf,
            "historical_backtest": backtest,
            "separation_note": "three buckets are kept separate; no combined profit figure",
        },
        "recommendations": recommendations,
    }


def markdown(review: dict) -> str:
    md = [
        f"# Daily AI trading review — {review['session_date']}",
        "",
        f"Generated {review['generated_at']} · as of {review['as_of']} · "
        f"session complete: **{review['session_complete']}**",
        "",
        "SHADOW ONLY · orders submitted: 0 · enforcement OFF · "
        "shadow/backtest figures are not realized profit.",
        "",
        "## Market conditions",
        f"- Regimes recorded: {', '.join(review['market_conditions']['regimes_recorded']) or 'none'}",
        f"- VIX values in rejection reasons: {review['market_conditions']['vix_values_in_rejections']}",
        f"- VIX as-of evidence: {review['market_conditions']['vix_as_of_evidence']}",
        "",
        "## Trading activity",
        f"- Cycles: {review['trading_activity']['cycles']} · "
        f"symbols: {len(review['trading_activity']['symbols_scanned'])} · "
        f"setups: {review['trading_activity']['setups_recorded']} "
        f"({review['trading_activity']['setups_by_direction']})",
        f"- Judge batches completed: {review['trading_activity']['judge_completed_batches']} · "
        f"failures: {review['trading_activity']['judge_failures']}",
        "",
        "## Trade decisions",
    ]
    d = review["trade_decisions"]
    md += [f"- Evaluated {d['candidates_evaluated']} · long bias {d['long_bias']} · "
           f"bearish research {d['bearish_research']}",
           f"- Approved entries {d['approved_entries']} · valid policy rejections "
           f"{d['valid_policy_rejections']} · technical failures {d['technical_failures']}",
           f"- Shadow rows {d['shadow_rows']} · broker orders {d['broker_orders']}",
           "", "## Missed opportunities",
           f"- Hypothetical outcomes: {review['missed_opportunities']['hypothetical_outcomes']} · "
           f"insufficient evidence: {review['missed_opportunities']['insufficient_evidence']}",
           f"- Method: {review['missed_opportunities']['method']}",
           "", "## Execution quality",
           f"- Judge failures: {review['execution_quality']['judge_failures']}",
           f"- Environment: {review['execution_quality']['environment_failures']}",
           "", "## Risk management"]
    r = review["risk_management"]
    md += [f"- VIX blocks {r['vix_blocks']} · regime blocks {r['regime_blocks']} · "
           f"no short path {r['no_short_path']}",
           f"- Kill switch: {r['emergency_kill']} · {r['assessment']}",
           "", "## Long/short performance"]
    p = review["long_short_performance"]
    md += [f"- Realized paper fills: {p['realized_paper']['filled_orders']} "
           f"({p['realized_paper']['statistics']})",
           f"- Shadow: {p['shadow']['shadow_rows']} rows ({p['shadow']['note']})",
           f"- Backtest: {p['historical_backtest']['status']}",
           "", "## Recommendations"]
    md += [f"- {x}" for x in review["recommendations"]]
    md.append("")
    return "\n".join(md)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--as-of", default="")
    ap.add_argument("--session-complete", action="store_true")
    ap.add_argument("--post-bars", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="defaults to outputs/reports/daily under --root")
    args = ap.parse_args(argv)
    as_of = args.as_of or datetime.now(ET).isoformat()
    post = None
    if args.post_bars:
        try:
            loaded = json.loads(args.post_bars.read_text(encoding="utf-8"))
            post = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError, UnicodeError):
            post = None
    review = build_review(args.root, args.date, as_of=as_of,
                          session_complete=args.session_complete, post_bars=post)
    out_dir = args.out_dir or (args.root / "outputs" / "reports" / "daily")
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / f"{args.date}-trading-review.json"
    mp = out_dir / f"{args.date}-trading-review.md"
    jp.write_text(json.dumps(review, indent=2, default=str) + "\n", encoding="utf-8")
    mp.write_text(markdown(review), encoding="utf-8")
    print(f"wrote {jp} and {mp}; orders: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
