#!/usr/bin/env python3
"""Read-only payload for the /trading Intraday Decision Pipeline panel.

Reads the newest generated daily review artifact. No broker calls, no writes,
no inference, no order submission. Missing evidence is reported explicitly,
never as "no opportunities".
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.opportunity_analysis import latest_report


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason,
            "orders_submitted": 0, "entry_permission": "none"}


def _path_for(row: dict) -> str:
    steps = ["Screened"]
    bias = str(row.get("analyst_bias", ""))
    if bias == "bearish_research":
        steps.append("Bearish Research")
    elif bias == "long":
        steps.append("Bullish Research")
    category = str(row.get("rejection_category", ""))
    if category == "no_short_entry_path":
        steps.append("Short Setup (no entry branch)")
    elif category in {"vix_policy", "regime_policy"}:
        steps.append("Risk Gate")
    if row.get("setup"):
        steps.append(str(row["setup"]))
    if category == "regime_policy":
        steps.append("RISK_OFF Block")
    elif category == "vix_policy":
        steps.append("VIX Block")
    classification = str(row.get("final_classification", ""))
    if classification == "valid_policy_rejection":
        steps.append("Shadow")
    elif classification == "technical_failure":
        steps.append("Technical Failure")
    return " -> ".join(steps)


def intraday_pipeline_payload(root: Path) -> dict:
    path = latest_report(root)
    if path is None:
        return _unavailable("no daily trading-review artifact")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return _unavailable("daily trading-review artifact unreadable")
    if not isinstance(doc, dict):
        return _unavailable("malformed daily trading-review artifact")

    activity = doc.get("trading_activity") or {}
    decisions = doc.get("trade_decisions") or {}
    risk = doc.get("risk_management") or {}
    perf = doc.get("long_short_performance") or {}
    rows = (doc.get("missed_opportunities") or {}).get("rows") or []
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidates.append({
            "symbol": str(row.get("symbol", "")),
            "bias": str(row.get("analyst_bias", "")),
            "category": str(row.get("rejection_category", "")),
            "classification": str(row.get("final_classification", "")),
            "reason": str(row.get("rejection_reason", ""))[:200],
            "path": _path_for(row),
            "technical": str(row.get("final_classification", "")) == "technical_failure",
        })
    cycles = activity.get("cycle_details") or []
    return {
        "status": "ok",
        "source": path.relative_to(root).as_posix(),
        "session_date": str(doc.get("session_date", "")),
        "as_of": str(doc.get("as_of", "")),
        "session_complete": bool(doc.get("session_complete", False)),
        "orders_submitted": 0,
        "entry_permission": "none - research and reporting only",
        "read_only": True,
        "market_data_status": "not independently verified - no freshness timestamp in source artifacts",
        "active_scan_cycle": str(cycles[-1])[:120] if cycles else "none",
        "symbols_screened": len(activity.get("symbols_scanned") or []),
        "long_candidates": int(decisions.get("long_bias") or 0),
        "short_candidates": int(decisions.get("bearish_research") or 0),
        "technical_setups": int(activity.get("setups_recorded") or 0),
        "ai_research_completed": int(activity.get("judge_completed_batches") or 0),
        "pending_research": "not verified - no pending-research artifact",
        "research_timeout_count": int(activity.get("judge_failures") or 0),
        "decision_latency": {"median_seconds": None, "p95_seconds": None,
                             "status": "not instrumented in live path"},
        "risk_approved": int(decisions.get("approved_entries") or 0),
        "risk_blocked": int(decisions.get("valid_policy_rejections") or 0),
        "shadow_only_candidates": int(decisions.get("shadow_rows") or 0),
        "authorized_orders": "not verified - enforcement OFF",
        "submitted_orders": int(decisions.get("broker_orders") or 0),
        "actual_fills": int((perf.get("realized_paper") or {}).get("filled_orders") or 0),
        "current_positions": "not verified - no broker position artifact in report",
        "regime": ", ".join((doc.get("market_conditions") or {}).get("regimes_recorded") or []) or "UNKNOWN",
        "risk_summary": {
            "vix_blocks": int(risk.get("vix_blocks") or 0),
            "regime_blocks": int(risk.get("regime_blocks") or 0),
            "no_short_path": int(risk.get("no_short_path") or 0),
        },
        "candidates": candidates,
        "note": ("Policy rejections are valid gate outcomes (amber); technical failures "
                 "are errors (red). Shadow results are not realized profit and unfilled "
                 "orders are not positions."),
    }
