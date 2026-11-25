#!/usr/bin/env python3
"""Read-only Operational Reliability payload for the /trading dashboard."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
from pathlib import Path

from robinhood_tools.workflow_manifest import read_manifest
from scripts.opportunity_analysis import _unavailable as _base_unavailable

WORKFLOWS = ("options-watch", "overnight", "flatten-verify",
             "news", "shadow-scoring", "intraday-loop")


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


def operational_reliability_payload(
        root: Path, *, date: str | None = None) -> dict:
    date = date or datetime.now(ET).date().isoformat()
    manifests = {name: read_manifest(root, name, date) for name in WORKFLOWS}
    if not any(manifests.values()):
        return {**_base_unavailable("no workflow manifests"),
                "workflows": {name: {"status": "NOT TESTED"} for name in WORKFLOWS}}
    graph = None
    for path in sorted((root / "outputs/shadow/research").glob("*/measurement.json")):
        doc = _load(path)
        if doc and doc.get("track") == "full_tradingagents_graph":
            graph = doc
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
        })
    return {
        "status": "ok",
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
            "status": graph.get("status") if graph else "NOT TESTED",
            "scope": graph.get("scope") if graph else "no graph artifact",
            "elapsed_seconds": graph.get("wall_seconds") if graph else None,
            "stage": (graph.get("progress") or {}).get("stage") if graph else None,
            "error": graph.get("error", "") if graph else "",
            "orders_submitted": 0,
        },
        "signal_integrity": {
            "factory_status": "ok" if signals else "unavailable",
            "counts": (signals or {}).get("counts") or {},
            "scout_factory": (compare or {}).get("counts") or {},
        },
        "broker_verification": {
            "source": "not queried by this endpoint",
            "positions": "see dashboard Positions panel",
            "orders": "see dashboard Working Orders panel",
        },
        "note": ("Manifest statuses are terminal workflow evidence. Shadow research "
                 "and replay results are never broker fills or realized profit."),
    }
