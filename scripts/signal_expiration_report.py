#!/usr/bin/env python3
"""Classify research decisions against the reviewable expiration specification.

Never invents missing timestamps. Reads the Phase 8.8 latency artifact and
applies EXPIRATION.md rules with first-match-wins priority.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ACTIONABLE = "ACTIONABLE_AT_DECISION"
EXPIRED = "EXPIRED_BEFORE_DECISION"
CUTOFF = "SESSION_CUTOFF_EXCEEDED"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
INELIGIBLE = "TECHNICALLY_INELIGIBLE"
CLASSES = (ACTIONABLE, EXPIRED, CUTOFF, INSUFFICIENT, INELIGIBLE)
REQUIRED = ("setup_formation_time", "last_completed_bar_time", "signal_detection_time",
            "research_queue_entry", "deterministic_gate_time", "authorization_time",
            "signal_expiration", "source_quote_age_at_decision_s")


def _ordered(row: dict) -> bool:
    fields = ("signal_detection_time", "research_queue_entry",
              "deterministic_gate_time", "decision_et")
    stamps = []
    for field in fields:
        value = row.get(field)
        if value is None:
            return False
        try:
            stamps.append(datetime.fromisoformat(str(value)))
        except ValueError:
            return False
    return all(a.astimezone() <= b.astimezone() for a, b in zip(stamps, stamps[1:]))


def classify(row: dict) -> dict:
    reasons = []
    setup = str(row.get("technical_setup") or row.get("setup") or "")
    if setup and setup not in {"long_reclaim", "short_breakdown"}:
        reasons.append("setup outside approved whitelist")
    if row.get("technically_eligible") is False or \
            row.get("bars_validated") is False or \
            row.get("feed_supported") is False or \
            row.get("future_dated_quote") is True:
        reasons.append("technical gate rejected")
    if reasons:
        return {"classification": INELIGIBLE, "reasons": reasons}

    if row.get("before_entry_cutoff") is None:
        return {"classification": INSUFFICIENT,
                "reasons": ["entry-cutoff evidence missing"]}
    if row["before_entry_cutoff"] is False:
        return {"classification": CUTOFF,
                "reasons": ["decision after 15:15 ET entry cutoff"]}

    missing = [field for field in REQUIRED if row.get(field) is None]
    if missing:
        return {"classification": INSUFFICIENT, "reasons": ["missing: " + ",".join(missing)]}
    if not _ordered(row):
        return {"classification": INSUFFICIENT,
                "reasons": ["timestamp order invalid"]}

    age = float(row["source_quote_age_at_decision_s"])
    if age < 0:
        return {"classification": EXPIRED, "reasons": ["quote age negative / future-dated"]}
    if age > 300:
        return {"classification": EXPIRED,
                "reasons": ["quote age exceeds 300 s freshness window"]}
    bar_age = row.get("bar_age_at_decision_s")
    if bar_age is not None and float(bar_age) > 1200:
        return {"classification": EXPIRED,
                "reasons": ["bar age exceeds 20 min staleness limit"]}
    return {"classification": ACTIONABLE, "reasons": []}

def analyze(latency_report: dict) -> dict:
    rows = []
    for observation in latency_report.get("observations", []):
        result = classify(observation)
        rows.append({"symbol": observation.get("symbol"),
                     "date": observation.get("date"),
                     "research_start_utc": observation.get("research_start_utc"),
                     "decision_et": observation.get("decision_et"),
                     **result})
    counts = {name: sum(1 for r in rows if r["classification"] == name)
              for name in CLASSES}
    return {
        "track": "signal_expiration_analysis",
        "generated_at": datetime.now(ET).isoformat(),
        "spec": "intraday-setups/v1.0.0/EXPIRATION.md",
        "source": latency_report.get("source", ""),
        "policy": latency_report.get("policy", {}),
        "observations": rows, "counts": counts,
        "actionable_rate": round(counts[ACTIONABLE] / len(rows), 4) if rows else None,
        "note": ("INSUFFICIENT_EVIDENCE is not proof of staleness. Only "
                 "ACTIONABLE_AT_DECISION may be counted as an actionable "
                 "opportunity; no entry permission is granted."),
        "entry_permission": "none - analysis only",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--latency", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    report = analyze(json.loads(args.latency.read_text(encoding="utf-8")))
    text = json.dumps(report, indent=2, default=str) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
