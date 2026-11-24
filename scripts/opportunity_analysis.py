#!/usr/bin/env python3
"""Read-only dashboard payload for the Trading Opportunity Analysis panel.

Reads the newest generated daily review artifact. No broker calls, no writes,
no order submission. A missing or broken artifact returns ``unavailable`` -
never an empty "no opportunities" result.
"""
from __future__ import annotations

import json
from pathlib import Path

PATTERN = "outputs/reports/daily/*-trading-review.json"


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason,
            "orders_submitted": 0, "entry_permission": "none"}


def latest_report(root: Path) -> Path | None:
    pattern = Path(root) / PATTERN
    try:
        matches = sorted(pattern.parent.glob(pattern.name),
                         key=lambda p: (p.stat().st_mtime, str(p)))
    except OSError:
        return None
    return matches[-1] if matches else None


def opportunity_analysis_payload(root: Path) -> dict:
    path = latest_report(root)
    if path is None:
        return _unavailable("no daily trading-review artifact")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return _unavailable("daily trading-review artifact unreadable")
    if not isinstance(doc, dict):
        return _unavailable("malformed daily trading-review artifact")

    rows = []
    for candidate in (doc.get("missed_opportunities") or {}).get("rows") or []:
        if not isinstance(candidate, dict):
            continue
        rows.append({
            "symbol": str(candidate.get("symbol", "")),
            "bias": str(candidate.get("analyst_bias", "")),
            "strategy": str(candidate.get("strategy", "")),
            "reason": str(candidate.get("rejection_reason", "")),
            "category": str(candidate.get("rejection_category", "")),
            "classification": str(candidate.get("final_classification", "")),
            "hypothetical": str((candidate.get("hypothetical_outcome") or {}).get("status", "not_determinable")),
            "data_quality": str(candidate.get("data_quality", "")),
        })

    decisions = doc.get("trade_decisions") or {}
    activity = doc.get("trading_activity") or {}
    risk = doc.get("risk_management") or {}
    perf = doc.get("long_short_performance") or {}
    summary = {
        "symbols_scanned": len(activity.get("symbols_scanned") or []),
        "long_candidates": int(decisions.get("long_bias") or 0),
        "short_candidates": int(decisions.get("bearish_research") or 0),
        "setups_recorded": int(activity.get("setups_recorded") or 0),
        "qualified_setups": int(activity.get("setups_recorded") or 0)
                            - int(decisions.get("approved_entries") or 0),
        "risk_blocked": int(decisions.get("valid_policy_rejections") or 0),
        "technical_failures": int(decisions.get("technical_failures") or 0),
        "shadow_opportunities": int(decisions.get("shadow_rows") or 0),
        "actual_entries": int(decisions.get("approved_entries") or 0),
        "actual_fills": int((perf.get("realized_paper") or {}).get("filled_orders") or 0),
    }
    return {
        "status": "ok",
        "generated_at": str(doc.get("generated_at", "")),
        "session_date": str(doc.get("session_date", "")),
        "as_of": str(doc.get("as_of", "")),
        "session_complete": bool(doc.get("session_complete", False)),
        "source": path.relative_to(root).as_posix(),
        "read_only": True,
        "orders_submitted": 0,
        "entry_permission": str(doc.get("entry_permission", "none")),
        "enforcement_enabled": bool(doc.get("enforcement_enabled", False)),
        "market_regime": ", ".join((doc.get("market_conditions") or {}).get("regimes_recorded") or []) or "UNKNOWN",
        "vix": (doc.get("market_conditions") or {}).get("vix_values_in_rejections") or [],
        "scanner_health": {
            "cycles": int(activity.get("cycles") or 0),
            "judge_failures": int(activity.get("judge_failures") or 0),
            "last_completed_scan": str(activity.get("cycle_details") or [""])[-1][:120],
        },
        "data_feed_health": ("not independently verified - no freshness timestamp in source artifacts"),
        "execution_readiness": ("not ready - enforcement OFF; no authorization enforcement path exercised"),
        "risk_summary": {
            "vix_blocks": int(risk.get("vix_blocks") or 0),
            "regime_blocks": int(risk.get("regime_blocks") or 0),
            "no_short_path": int(risk.get("no_short_path") or 0),
            "kill_switch": str(risk.get("emergency_kill", "not_recorded")),
        },
        "summary": summary,
        "opportunities": rows,
        "note": ("Risk rejections are valid gate outcomes; technical failures are errors. "
                 "Shadow and backtest figures are simulated, not realized broker profit. "
                 "No order is implied by any row."),
    }
