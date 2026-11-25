#!/usr/bin/env python3
"""Honest signal-to-decision latency report from available real artifacts.

Reads actual research log pairs and auto.json quote times from an operational
checkout. Missing timestamps are reported as missing; nothing is fabricated.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, time
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ET = ZoneInfo("America/New_York")
CUTOFF = time(15, 15)
FRESHNESS_SECONDS = 300.0
LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.+-]+)\s+\[(?P<symbol>[A-Z0-9.-]+)\]\s+"
    r"(?P<kind>researching|decision:)")


def parse_log(log: Path, date: str) -> list[dict]:
    if not log.is_file():
        return []
    pending: dict[str, datetime] = {}
    rows = []
    for raw in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(raw)
        if not m or not raw.startswith(date):
            continue
        ts = datetime.fromisoformat(m.group("ts"))
        symbol = m.group("symbol")
        if m.group("kind") == "researching":
            pending[symbol] = ts
        elif symbol in pending:
            start = pending.pop(symbol)
            rows.append({"symbol": symbol, "date": date,
                         "research_start_utc": start.astimezone(ZoneInfo("UTC")).isoformat(),
                         "research_end_utc": ts.astimezone(ZoneInfo("UTC")).isoformat(),
                         "research_duration_s": round((ts - start).total_seconds(), 3)})
    return rows


def quote_age(live: Path, symbol: str, date: str, decision_utc: str) -> float | None:
    path = live / "outputs/paper/tradingagents" / symbol / date / "auto.json"
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    raw = doc.get("price_as_of")
    if not raw:
        return None
    try:
        quoted = datetime.fromisoformat(str(raw))
        decision = datetime.fromisoformat(decision_utc)
        return round((decision - quoted).total_seconds(), 3)
    except ValueError:
        return None


def report(live: Path, date: str) -> dict:
    rows = parse_log(live / "outputs/paper/tradingagents/live.log", date)
    for row in rows:
        row["source_quote_age_at_decision_s"] = quote_age(
            live, row["symbol"], date, row["research_end_utc"])
        decision_et = datetime.fromisoformat(row["research_end_utc"]).astimezone(ET)
        cutoff = datetime.combine(decision_et.date(), CUTOFF, tzinfo=ET)
        row["decision_et"] = decision_et.isoformat()
        row["cutoff_margin_s"] = round((cutoff - decision_et).total_seconds(), 3)
        row["before_entry_cutoff"] = decision_et <= cutoff
        row["quote_age_within_300s"] = (
            None if row["source_quote_age_at_decision_s"] is None
            else 0 <= row["source_quote_age_at_decision_s"] <= FRESHNESS_SECONDS)
    durations = [r["research_duration_s"] for r in rows]
    ages = [r["source_quote_age_at_decision_s"] for r in rows
            if r["source_quote_age_at_decision_s"] is not None]
    return {
        "track": "signal_to_decision_latency", "session_date": date,
        "generated_at": datetime.now(ET).isoformat(),
        "source": str(live / "outputs/paper/tradingagents/live.log"),
        "evidence_level": "ACTUAL_RESEARCH_LOG (not full signal-to-decision)",
        "policy": {"entry_cutoff_et": "15:15", "freshness_seconds": 300,
                   "flatten_et": "15:45"},
        "observations": rows,
        "counts": {"observations": len(rows),
                   "before_cutoff": sum(1 for r in rows if r["before_entry_cutoff"]),
                   "quote_age_available": len(ages),
                   "quote_age_within_300s": sum(1 for r in rows
                                                if r["quote_age_within_300s"] is True)},
        "research_duration_s": {
            "median": round(median(durations), 3) if durations else None,
            "min": min(durations) if durations else None,
            "max": max(durations) if durations else None,
        },
        "missing_fields": ["setup_formation_time", "last_completed_bar_time",
                           "signal_detection_time", "research_queue_entry",
                           "deterministic_gate_time", "authorization_time",
                           "signal_expiration"],
        "note": ("Source quote age is not signal detection age. The 300 s window "
                 "applies to signal freshness; a quote older than 300 s cannot "
                 "support a live entry. Missing timestamps are NOT_VERIFIED, "
                 "never estimated."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live-root", type=Path, required=True)
    ap.add_argument("--date", default=datetime.now(ET).date().isoformat())
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    result = report(Path(args.live_root), args.date)
    text = json.dumps(result, indent=2, default=str) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps(result["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
