#!/usr/bin/env python3
"""Append-only signal-to-decision latency ledger.

UTC timestamps for persisted events, monotonic nanoseconds for durations.
No secrets, prompts or tool outputs are stored — only allowlisted stage names,
identifiers and evidence references.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "signal-latency-v1"
ALLOWED_STAGES = {
    "setup_formation", "last_bar", "signal_detected", "research_queued",
    "research_start", "research_end", "deterministic_gate", "authorization",
    "expiration", "cutoff_evaluated", "decision",
}


def ledger_path(root: Path, date: str, run_id: str) -> Path:
    if not date or any(c not in "0123456789-" for c in date):
        raise ValueError("invalid date")
    if not run_id or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
                         for c in run_id):
        raise ValueError("invalid run_id")
    return Path(root) / "outputs" / "latency" / date / f"{run_id}.jsonl"


def record(root: Path, date: str, run_id: str, *, symbol: str, stage: str,
           status: str = "ok", duration_ms: float | None = None,
           evidence_ref: str = "", monotonic_ns: int | None = None) -> dict:
    if stage not in ALLOWED_STAGES:
        raise ValueError(f"invalid stage: {stage!r}")
    event = {
        "schema": SCHEMA, "event_id": uuid.uuid4().hex, "run_id": run_id,
        "symbol": str(symbol).upper()[:16], "stage": stage,
        "status": status, "utc": datetime.now(timezone.utc).isoformat(),
        "monotonic_ns": monotonic_ns if monotonic_ns is not None
                        else time.perf_counter_ns(),
        "duration_ms": duration_ms, "evidence_ref": str(evidence_ref)[:200],
    }
    path = ledger_path(root, date, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event


def load(root: Path, date: str, run_id: str) -> list[dict]:
    path = ledger_path(root, date, run_id)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if doc.get("schema") == SCHEMA:
            events.append(doc)
    return sorted(events, key=lambda e: e.get("monotonic_ns", 0))


def analyze(events: list[dict]) -> dict:
    """Derive metrics only from stages that are actually present."""
    by_stage: dict[str, dict] = {}
    for event in events:
        by_stage.setdefault(event["stage"], event)
    def delta(start: str, end: str) -> float | None:
        if start not in by_stage or end not in by_stage:
            return None
        return round((by_stage[end]["monotonic_ns"] -
                      by_stage[start]["monotonic_ns"]) / 1e6, 6)
    return {
        "stages_present": sorted(by_stage),
        "bar_to_detection_ms": delta("last_bar", "signal_detected"),
        "detection_to_research_ms": delta("signal_detected", "research_start"),
        "research_duration_ms": delta("research_start", "research_end"),
        "research_to_gate_ms": delta("research_end", "deterministic_gate"),
        "total_signal_to_decision_ms": delta("signal_detected", "decision"),
        "age_at_decision_ms": delta("last_bar", "decision"),
        "caveat": "Stages not recorded are reported as null, never estimated.",
    }
