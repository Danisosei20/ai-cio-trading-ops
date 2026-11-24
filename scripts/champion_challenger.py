#!/usr/bin/env python3
"""Deterministic champion vs shadow-challenger research comparison.

Free-form LLM wording is never compared directly. Compare normalized decisions,
validated fact keys, source references, evidence completeness, latency and
failure counts. Disagreements are recorded, never resolved automatically.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MIN_SESSIONS = 5
MIN_SYMBOLS = 50

NORMALIZED_DECISIONS = {
    "buy": "bullish", "overweight": "bullish", "call": "bullish",
    "sell": "bearish", "underweight": "bearish", "put": "bearish",
    "hold": "neutral", "review": "neutral", "unknown": "unknown",
}


def normalize_decision(value: str) -> str:
    return NORMALIZED_DECISIONS.get(str(value or "").strip().lower(), "unknown")


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * fraction))
    return round(ordered[idx], 6)


def _latency(outcomes: list[dict]) -> dict:
    # Outcomes without any timing fields are not latency measurements.
    values = [float(o.get("queue_wait_seconds", 0)) +
              float(o.get("request_seconds", 0)) for o in outcomes
              if isinstance(o, dict) and
              ("queue_wait_seconds" in o or "request_seconds" in o)]
    return {
        "count": len(values),
        "mean_seconds": round(sum(values) / len(values), 6) if values else None,
        "median_seconds": _percentile(values, 0.5),
        "p95_seconds": _percentile(values, 0.95),
        "max_seconds": round(max(values), 6) if values else None,
    }


def compare(champion: dict, challenger: dict) -> dict:
    c_rows = champion.get("outcomes") or []
    h_rows = challenger.get("outcomes") or []
    by_symbol = {str(o.get("symbol")): o for o in c_rows if isinstance(o, dict)}
    disagreements, comparisons = [], []
    for row in h_rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol"))
        champ = by_symbol.get(symbol)
        if champ is None:
            disagreements.append({"symbol": symbol, "reason": "no champion result"})
            continue
        c_dec, h_dec = (normalize_decision(champ.get("decision")),
                        normalize_decision(row.get("decision")))
        c_ev = {str(k) for k in (champ.get("evidence_keys") or [])}
        h_ev = {str(k) for k in (row.get("evidence_keys") or [])}
        entry = {
            "symbol": symbol,
            "champion_decision": c_dec,
            "challenger_decision": h_dec,
            "champion_raw": champ.get("decision"),
            "challenger_raw": row.get("decision"),
            "decision_agreement": c_dec == h_dec,
            "evidence_agreement": c_ev == h_ev,
            "missing_in_challenger": sorted(c_ev - h_ev),
            "extra_in_challenger": sorted(h_ev - c_ev),
            "champion_status": champ.get("status"),
            "challenger_status": row.get("status"),
        }
        comparisons.append(entry)
        if not entry["decision_agreement"] or not entry["evidence_agreement"]:
            disagreements.append(entry)

    c_sessions = int(champion.get("sessions") or 0)
    h_sessions = int(challenger.get("sessions") or 0)
    sufficient = (c_sessions >= MIN_SESSIONS and h_sessions >= MIN_SESSIONS and
                  len(comparisons) >= MIN_SYMBOLS)
    return {
        "generated_at": datetime.now(ET).isoformat(),
        "champion": {"mode": champion.get("mode", "sequential"),
                     "latency": _latency(c_rows),
                     "timeouts": sum(o.get("status") == "timeout" for o in c_rows),
                     "failures": sum(o.get("status") == "error" for o in c_rows)},
        "challenger": {"mode": challenger.get("mode", "laboratory"),
                       "latency": _latency(h_rows),
                       "timeouts": sum(o.get("status") == "timeout" for o in h_rows),
                       "failures": sum(o.get("status") == "error" for o in h_rows)},
        "compared_symbols": len(comparisons),
        "decision_agreement_rate": round(
            sum(x["decision_agreement"] for x in comparisons) / len(comparisons), 4)
        if comparisons else None,
        "disagreements": disagreements,
        "evidence_completeness_gap": sum(len(x["missing_in_challenger"]) for x in comparisons),
        "evaluation_window": {"champion_sessions": c_sessions,
                              "challenger_sessions": h_sessions,
                              "min_sessions": MIN_SESSIONS,
                              "min_symbols": MIN_SYMBOLS,
                              "sufficient": sufficient},
        "promotion_recommendation": "none - automatic promotion disabled",
        "recommendation": ("insufficient multi-session evidence; keep champion" if not sufficient
                           else "owner review required; no automatic routing change"),
        "shadow_only": True,
        "orders_submitted": 0,
        "entry_permission": "none",
        "note": "Free-form wording differences are ignored; normalized decisions and evidence keys are compared.",
    }


def write_comparison(result: dict, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
