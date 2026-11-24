#!/usr/bin/env python3
"""Read-only payload for the Intraday Signal Intelligence dashboard panel."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from robinhood_tools.market_snapshot import verify_snapshot  # noqa: E402
from scripts.opportunity_analysis import _unavailable as _base_unavailable


def _unavailable(reason: str) -> dict:
    return {
        **_base_unavailable(reason),
        "market_evidence": {"snapshots": 0, "full_graph": {"status": "not verified"}},
        "broker_activity": {"authorized_orders": "not verified",
                            "submitted_paper_orders": "not verified",
                            "filled_paper_orders": "not verified",
                            "positions": "not verified"},
    }


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


def _pct(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))], 3)


def signal_intelligence_payload(root: Path, *, date: str = "2026-11-24") -> dict:
    factory = _load(_latest(root, "outputs/reports/signals/*signal-factory*.json"))
    capability = _load(_latest(root, "outputs/reports/diagnostics/*capability*.json"))
    scoring = _load(_latest(root, "outputs/reports/scoring/*scoring*.json"))
    benchmark = _load(_latest(root, "outputs/reports/research/*research-pipeline-benchmark.json"))
    lineage = _load(_latest(root, "outputs/reports/short/*short-signal-lineage.json"))
    lifecycle = _load(_latest(root, "outputs/reports/daily/*eod-lifecycle.json"))
    fullgraph = _load(_latest(root, "outputs/shadow/research/fullgraph-*/measurement.json"))
    scout_compare = _load(_latest(root, "outputs/reports/comparison/*scout-factory*.json"))
    if all(x is None for x in (factory, capability, scoring, benchmark, lineage,
                               fullgraph, scout_compare)):
        return _unavailable("no signal-intelligence artifact")

    rows = (factory or {}).get("candidates") or []
    from scripts.signal_archive import verify_archive  # noqa: PLC0415
    date = str((factory or {}).get("generated_at", date))[:10]
    arch_dir = Path(root) / "outputs/signals/archive" / date
    archived = [p for p in (sorted(arch_dir.glob("*.json"))
                            if arch_dir.is_dir() else [])
                if verify_archive(p)[0]]
    champ = (benchmark or {}).get("champion") or {}
    chall = (benchmark or {}).get("challenger") or {}
    return {
        "status": "ok" if all(x is not None for x in (
            factory, capability, scoring, benchmark, lineage)) else "partial",
        "generated_at": datetime.now().astimezone().isoformat(),
        "session_date": date,
        "read_only": True,
        "orders_submitted": 0,
        "entry_permission": "none - observation only",
        "technical_candidates": {
            "status": "ok" if factory else "unavailable",
            "valid_long": sum(r.get("direction") == "long" and
                              r.get("validation_status") == "validated" for r in rows),
            "valid_short": sum(r.get("direction") == "short" and
                               r.get("validation_status") == "validated" for r in rows),
            "rejected": sum(r.get("validation_status") == "rejected" for r in rows),
            "rejections": [r.get("rejection_reason") for r in rows
                           if r.get("rejection_reason")][:10],
        },
        "point_in_time_archive": {"files": len(archived),
                                  "strategy_version": (factory or {}).get("strategy_version")},
        "data_feed_capability": {
            "status": "ok" if capability else "unavailable",
            "feed": (capability or {}).get("snapshot", {}).get("feed"),
            "volume_basis": (capability or {}).get("snapshot", {}).get("volume_basis"),
            "strategies": {k: v.get("status") for k, v in
                           (capability or {}).get("strategies", {}).items()},
            "requires_sip": (capability or {}).get("requires_sip") or [],
        },
        "research_latency": {
            "status": "ok" if benchmark else "unavailable",
            "mode": (benchmark or {}).get("mode"),
            "champion_wall_seconds": champ.get("wall_seconds"),
            "challenger_wall_seconds": chall.get("wall_seconds"),
            "champion_median": (champ.get("latency") or {}).get("median_seconds"),
            "champion_p95": (champ.get("latency") or {}).get("p95_seconds"),
            "challenger_median": (chall.get("latency") or {}).get("median_seconds"),
            "challenger_p95": (chall.get("latency") or {}).get("p95_seconds"),
            "speedup": (benchmark or {}).get("speedup"),
            "completion_champion": champ.get("completed"),
            "completion_challenger": chall.get("completed"),
            "benchmark_at": (benchmark or {}).get("generated_at"),
        },
        "opportunity_scores": {
            "status": "ok" if scoring else "unavailable",
            "results": (scoring or {}).get("results") or [],
            "out_of_sample_sufficient": (scoring or {}).get("evaluation", {}).get(
                "out_of_sample_sufficient", False),
        },
        "short_lineage": {
            "status": "ok" if lineage else "unavailable",
            "counts": (lineage or {}).get("counts") or {},
        },
        "broker_activity": {
            "authorized_orders": "not verified",
            "submitted_paper_orders": "not verified",
            "filled_paper_orders": "not verified",
            "positions": "not verified",
            "note": "This endpoint does not query the broker; see the dashboard Orders panel.",
        },
        "market_evidence": {
            "snapshots": sum(1 for p in
                             (Path(root) / "outputs/market_snapshots").glob("*/*/*/snapshot.json")
                             if verify_snapshot(p)[0]),
            "full_graph": {
                "status": ("ok" if fullgraph and not fullgraph.get("error") and
                           isinstance(fullgraph.get("elapsed_seconds"), (int, float))
                           else "not verified"),
                "scope": (fullgraph or {}).get("scope"),
                "elapsed_seconds": (fullgraph or {}).get("elapsed_seconds"),
                "decision": (fullgraph or {}).get("decision"),
                "error": (fullgraph or {}).get("error"),
            },
        },
        "scout_vs_factory": {
            "status": "ok" if scout_compare else "unavailable",
            "counts": (scout_compare or {}).get("counts") or {},
            "note": (scout_compare or {}).get("note", "") if scout_compare else
                    "no comparison artifact",
        },
        "eod_status": {e.get("event"): e.get("status") for e in
                       (lifecycle or {}).get("events", [])},
        "note": ("Technical candidates, research recommendations, shadow opportunities "
                 "and broker activity are separate categories. Shadow results are never "
                 "realized profit."),
    }
