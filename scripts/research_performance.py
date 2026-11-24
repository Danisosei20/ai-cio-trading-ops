#!/usr/bin/env python3
"""Read-only payload for Research Performance & Short Opportunity Diagnostics.

Derives champion latency from live research log timestamps, challenger latency
from shadow batch artifacts, short diagnostics from the shadow routing report,
and EOD status from the lifecycle artifact. No broker writes, no orders, no
inference. Shadow results are never labelled as realized trading performance.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from scripts.opportunity_analysis import _unavailable

LOG = "outputs/paper/tradingagents/live.log"


def _latest(root: Path, pattern: str) -> Path | None:
    try:
        matches = sorted(Path(root).glob(pattern),
                         key=lambda p: (p.stat().st_mtime, str(p)))
    except OSError:
        return None
    return matches[-1] if matches else None


def _load(path: Path | None):
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _percentile(values: list[float], fraction: float):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))], 3)


def champion_latency(root: Path, date: str) -> dict:
    path = Path(root) / LOG
    if not path.is_file():
        return {"status": "unavailable", "reason": "no research log"}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"status": "unavailable", "reason": "research log unreadable"}
    started: dict[str, datetime] = {}
    durations: list[float] = []
    pattern = re.compile(r"^(\S+)\s+\[([A-Z.]+)\]\s+(researching|decision)\b")
    for line in lines:
        if not line.startswith(date):
            continue
        match = pattern.match(line)
        if not match:
            continue
        stamp, symbol, phase = match.groups()
        try:
            ts = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if phase == "researching":
            started[symbol] = ts
        elif symbol in started:
            durations.append(max(0.0, (ts - started.pop(symbol)).total_seconds()))
    return {
        "status": "ok",
        "source": LOG,
        "completed": len(durations),
        "median_seconds": _percentile(durations, 0.5),
        "p95_seconds": _percentile(durations, 0.95),
        "max_seconds": round(max(durations), 3) if durations else None,
        "measurement": "live research log timestamps (monotonic-equivalent ET wall clock)",
    }


def research_performance_payload(root: Path, *, date: str = "2026-11-24") -> dict:
    champion = champion_latency(root, date)
    challenger_doc = _load(_latest(root, "outputs/shadow/research/*/batch.json"))
    routing_doc = _load(_latest(root, "outputs/reports/shadow/*bearish-routing.json"))
    lifecycle_doc = _load(_latest(root, "outputs/reports/daily/*eod-lifecycle.json"))
    lineage_doc = _load(_latest(root, "outputs/reports/short/*short-signal-lineage.json"))
    health_doc = _load(_latest(root, "outputs/reports/diagnostics/*market-data-health.json"))
    nim_doc = _load(_latest(root, "outputs/reports/research/*nim-live-benchmark.json"))
    if all(x is None for x in (challenger_doc, routing_doc, lifecycle_doc,
                               lineage_doc, health_doc, nim_doc)) and \
            champion.get("status") != "ok":
        return _unavailable("no research or shadow diagnostic artifacts")

    challenger_latency = {"status": "unavailable", "reason": "no challenger artifact"}
    if challenger_doc:
        outcomes = challenger_doc.get("outcomes") or []
        vals = sorted(float(o.get("queue_wait_seconds", 0)) +
                      float(o.get("request_seconds", 0))
                      for o in outcomes if isinstance(o, dict) and
                      ("queue_wait_seconds" in o or "request_seconds" in o))
        challenger_latency = {
            "status": "ok", "mode": challenger_doc.get("mode", "laboratory"),
            "batch_wall_seconds": challenger_doc.get("batch_wall_seconds"),
            "completed": challenger_doc.get("completed"),
            "timeouts": challenger_doc.get("timeouts"),
            "median_seconds": _percentile(vals, 0.5),
            "p95_seconds": _percentile(vals, 0.95),
            "source": "shadow challenger batch artifact",
        }
        if challenger_latency["mode"] == "laboratory":
            challenger_latency["measurement"] = "LABORATORY ONLY - not live NIM latency"

    shadow_rows = (routing_doc or {}).get("candidates") or []
    rejection_counts: dict[str, int] = {}
    for row in shadow_rows:
        for item in row.get("rejections") or []:
            code = str(item.get("code", "unknown"))
            rejection_counts[code] = rejection_counts.get(code, 0) + 1
    short_diagnostics = {
        "status": "ok" if routing_doc else "unavailable",
        "source": (str(_latest(root, "outputs/reports/shadow/*bearish-routing.json")
                       .relative_to(root)).replace("\\", "/")
                   if routing_doc else None),
        "bearish_decisions": len(shadow_rows),
        "technical_evaluations": sum(1 for r in shadow_rows
                                     if any(x.get("gate") == "setup" for x in r.get("rejections") or [])),
        "valid_shadow_setups": sum(r.get("decision_class") == "shadow_candidate"
                                   for r in shadow_rows),
        "rejections_by_reason": rejection_counts,
        "borrow_failures": rejection_counts.get("short_eligibility_failed", 0),
        "regime_blocks": rejection_counts.get("regime_policy", 0),
        "data_quality_failures": rejection_counts.get("missing_setup_or_equity", 0) +
                                 rejection_counts.get("setup_direction_mismatch", 0),
    }
    eod_events = (lifecycle_doc or {}).get("events") or []
    return {
        "status": "ok",
        "generated_at": datetime.now().astimezone().isoformat(),
        "session_date": date,
        "read_only": True,
        "orders_submitted": 0,
        "entry_permission": "none - research and diagnostics only",
        "champion": champion,
        "challenger": challenger_latency,
        "comparison_status": ("laboratory only - champion unchanged" if
                              challenger_latency.get("mode") == "laboratory" else
                              "requires multi-session evaluation"),
        "short_diagnostics": short_diagnostics,
        "short_lineage": {
            "status": "ok" if lineage_doc else "unavailable",
            "counts": (lineage_doc or {}).get("counts") or {},
            "note": (lineage_doc or {}).get("note") or "no lineage artifact",
        },
        "market_data": {
            "status": "ok" if health_doc else "unavailable",
            "categories": (health_doc or {}).get("categories") or {},
            "permitted_latest_feeds": (health_doc or {}).get("permitted_latest_feeds") or [],
            "note": (health_doc or {}).get("note") or "no market-data diagnostic artifact",
        },
        "nim_benchmark": {
            "status": "ok" if nim_doc else "unavailable",
            "mode": (nim_doc or {}).get("mode"),
            "speedup": (nim_doc or {}).get("speedup"),
            "sequential_wall_seconds": (nim_doc or {}).get("sequential_wall_seconds"),
            "parallel_wall_seconds": (nim_doc or {}).get("parallel_wall_seconds"),
            "parallel_responses": (nim_doc or {}).get("parallel_responses"),
            "parallel_timeouts": (nim_doc or {}).get("parallel_timeouts"),
            "generated_at": (nim_doc or {}).get("generated_at"),
            "promotion": "none - benchmark no promotion",
        },
        "operational": {
            "scanner_health": "see Intraday Decision Pipeline",
            "model_provider": "nvidia/nemotron-3.5-lightning-30b-a3b",
            "broker_connection": ((lifecycle_doc or {}).get("broker") or {}).get("source")
                                 or "not verified",
            "deterministic_gate_verdict": "not exposed by this read-only endpoint",
            "authorization_enforcement": "OFF operationally",
            "strategy_permissions": "unchanged - no new entries authorized",
            "last_eod_verification": (lifecycle_doc or {}).get("generated_at"),
            "eod_event_status": {e.get("event"): e.get("status") for e in eod_events},
        },
        "note": ("NIM benchmark labelled live_nim only when real provider requests "
                 "succeeded; it measures provider latency, not research conclusions. "
                 "Shadow decisions are not submitted orders or realized profit."),
    }
