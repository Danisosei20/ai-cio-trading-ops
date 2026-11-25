#!/usr/bin/env python3
"""Read-only Operational Reliability payload for the /trading dashboard.

All values come from local manifests/artifacts or a static, read-only task
inventory. Nothing here contacts a broker or submits an order.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from robinhood_tools.workflow_manifest import read_manifest
from scripts.opportunity_analysis import _unavailable as _base_unavailable

ET = ZoneInfo("America/New_York")
WORKFLOWS = ("options-watch", "overnight", "flatten-verify",
             "news", "shadow-scoring", "intraday-loop")
SPEC_VERSION = "intraday-setups/v1.0.0"

# Read-only inventory from Phase 8.7 discovery. The registered tasks currently
# point at the LIVE checkout, which does not contain the Phase 8.6 launchers.
TASK_INVENTORY = (
    ("AI-Desk-OvernightExit", "overnight_exit.bat", "09:35", "TA venv"),
    ("AI-CIO-AutoTrade-0945", "auto_trade_weekday.bat", "09:45", "TA venv"),
    ("AI-Desk-Watchdog-0950", "watchdog_windows.bat", "09:50", "desk .venv"),
    ("AI-Desk-IntradayLoop", "intraday_loop.bat", "10:00", "TA venv"),
    ("AI-Desk-OptionsWatch-1510", "options_watch.bat", "15:10", "TA venv"),
    ("AI-Desk-OvernightEnter", "overnight_enter.bat", "15:40", "TA venv"),
    ("AI-Desk-Flatten-1545", "flatten_1545.bat", "15:45", "desk .venv"),
    ("AI-Desk-NewsReadout-1610", "news_daily.bat", "16:10", "desk .venv"),
    ("AI-Desk-ScoreShadows-1630", "score_shadow.bat", "16:30", "TA/desk"),
    ("AI-Desk-WeeklyReview-1645", "weekly_review.bat", "Fri 16:45", "desk .venv"),
)


def _latest(root: Path, pattern: str) -> Path | None:
    try:
        matches = sorted(root.glob(pattern), key=lambda p: (p.stat().st_mtime, str(p)))
    except OSError:
        return None
    return matches[-1] if matches else None


def _load(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _measurements(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((root / "outputs/shadow/research").glob("*/measurement.json")):
        doc = _load(path)
        if not doc:
            continue
        track = str(doc.get("track", ""))
        label = ("champion_equivalent" if track == "champion_equivalent_tradingagents_graph"
                 else "reduced" if track == "full_tradingagents_graph" else track)
        current = out.get(label)
        if current is None or str(doc.get("generated_at", "")) > str(
                current.get("generated_at", "")):
            out[label] = doc
    return out


def _uncertain_intents(root: Path) -> list[dict]:
    """Unconfirmed submission intents; never auto-retried by this service."""
    try:
        from robinhood_tools.execution_audit_gate import (  # noqa: PLC0415
            pending_reconciliation)
        return [dict(item) for item in pending_reconciliation(root)][:20]
    except Exception:  # noqa: BLE001 - diagnostics fail closed
        return [{"status": "unavailable"}]


def operational_reliability_payload(
        root: Path, *, date: str | None = None) -> dict:
    date = date or datetime.now(ET).date().isoformat()
    root = Path(root)
    manifests = {name: read_manifest(root, name, date) for name in WORKFLOWS}
    if not any(manifests.values()):
        return {**_base_unavailable("no workflow manifests"),
                "workflows": {name: {"status": "NOT TESTED"} for name in WORKFLOWS},
                "canonical_spec_version": SPEC_VERSION}
    measures = _measurements(root)
    reduced = measures.get("reduced") or {}
    champion = measures.get("champion_equivalent") or {}
    signals = _load(_latest(root, "outputs/reports/signals/*signal-factory*.json"))
    compare = _load(_latest(root, "outputs/reports/comparison/*scout-factory*.json"))
    rows = []
    for name in WORKFLOWS:
        doc = manifests.get(name) or {}
        rows.append({
            "task": name,
            "status": doc.get("status", "NOT TESTED"),
            "exit_code": doc.get("exit_code"),
            "finished_at": doc.get("finished_at"),
            "run_id": doc.get("run_id"),
            "error": doc.get("error", ""),
            "evidence": doc.get("evidence", {}),
        })
    flatten = manifests.get("flatten-verify") or {}
    overnight = manifests.get("overnight") or {}
    research = {}
    for label, doc in (("reduced", reduced), ("champion_equivalent", champion)):
        if doc:
            research[label] = {
                "status": doc.get("status"),
                "scope": doc.get("scope"),
                "elapsed_seconds": doc.get("wall_seconds") or doc.get("elapsed_seconds"),
                "graph_seconds": doc.get("graph_seconds"),
                "stage": (doc.get("progress") or {}).get("stage"),
                "provider": doc.get("provider"), "model": doc.get("model"),
                "config_digest": doc.get("config_digest"),
                "config_match": doc.get("config_match"),
                "provider_errors": (doc.get("counts") or {}).get("llm_errors"),
                "tool_calls": (doc.get("counts") or {}).get("tool_calls"),
                "evidence_complete": doc.get("evidence_complete"),
                "decision": doc.get("decision"),
                "error": doc.get("error", ""),
            }
    return {
        "status": ("degraded" if any(r["status"] == "FAILED" for r in rows)
                   else "ok"),
        "generated_at": datetime.now(ET).isoformat(),
        "session_date": date,
        "read_only": True,
        "orders_submitted": 0,
        "entry_permission": "none - diagnostics only",
        "workflows": rows,
        "last_successful": [r["finished_at"] for r in rows
                            if r["status"] in {"VERIFIED", "NO_ACTION_REQUIRED"}],
        "last_failed": [r["task"] for r in rows if r["status"] == "FAILED"],
        "research_graph": {
            "status": reduced.get("status") if reduced else "NOT TESTED",
            "scope": reduced.get("scope") if reduced else "no graph artifact",
            "elapsed_seconds": reduced.get("wall_seconds") if reduced else None,
            "stage": (reduced.get("progress") or {}).get("stage") if reduced else None,
            "error": reduced.get("error", "") if reduced else "",
            "orders_submitted": 0,
        },
        "research": research,
        "scheduler": {
            "logging_coverage": "unavailable - Task Scheduler Operational log disabled",
            "tasks": [{"name": n, "launcher": b, "scheduled": t, "interpreter": p}
                      for n, b, t, p in TASK_INVENTORY],
            "deployment_warning": ("Registered task commands point at the live checkout; "
                                   "worktree launcher fixes are not active until owner "
                                   "updates scheduler definitions"),
            "parent_child_rc": "captured by hardened launchers; see certification report",
        },
        "signal_integrity": {
            "factory_status": "ok" if signals else "unavailable",
            "counts": (signals or {}).get("counts") or {},
            "scout_factory": (compare or {}).get("counts") or {},
            "canonical_spec_version": SPEC_VERSION,
            "validation": "shadow only; no promotion or live rule change",
        },
        "risk_and_broker": {
            "authorization_mode": "enforcement OFF operationally",
            "flatten_status": flatten.get("status", "NOT TESTED"),
            "flatten_scope": (flatten.get("evidence") or {}).get("flat_scope", ""),
            "overnight_execution_mode": (overnight.get("evidence") or {}).get(
                "execution_mode", "not recorded"),
            "unresolved_orders": len((flatten.get("evidence") or {}).get(
                "open_order_statuses", {})),
            "uncertain_intents": _uncertain_intents(root),
            "source": "manifest evidence only; not a broker query",
        },
        "broker_verification": {
            "source": "not queried by this endpoint",
            "positions": "see dashboard Positions panel",
            "orders": "see dashboard Working Orders panel",
        },
        "deployment_readiness": {
            "parity": _load(root / "docs/certification/phase-8-8-parity-data.json")
                      or {"counts": {}},
            "plan": _load(root / "docs/certification/phase-8-8-deployment-plan.json")
                    or {"mode": "unavailable", "applied": False},
            "owner_authorization": "required - no live checkout changes in this phase",
        },
        "research_observability": {
            "measurements": {k: {"status": v.get("status"),
                                 "tool_counts": v.get("counts"),
                                 "tool_events": len(v.get("tool_events") or []),
                                 "evidence_complete": v.get("evidence_complete"),
                                 "wall_seconds": v.get("wall_seconds")}
                             for k, v in measures.items()},
            "telemetry_policy": ("model requests, tool starts/ends/errors only; "
                                 "no prompts, secrets or tool outputs recorded"),
        },
        "latency": _load(root / "docs/certification/phase-8-8-signal-latency.json")
                    or {"status": "NOT VERIFIED"},
        "strategy_validation": {
            "spec_version": SPEC_VERSION,
            "replay_status": "NOT VERIFIED - insufficient point-in-time data",
            "oos_status": "NOT VERIFIED - no frozen multi-session archive",
            "promotion": "none",
        },
        "evidence_labels": {
            "simulated": "rehearsal fixtures, never broker fills",
            "laboratory": "stubbed provider research, not live market evidence",
            "shadow": "research comparisons, no orders and no promotion",
            "paper": "actual Alpaca paper state when broker-verified",
            "live": "never enabled in this phase",
        },
        "note": ("Manifest statuses are terminal workflow evidence. Shadow research "
                 "and replay results are never broker fills or realized profit."),
    }
