#!/usr/bin/env python3
"""Audit short-signal lineage from bars to shadow decision (read-only).

Enforces: symbol identity, setup direction, timestamp integrity and
signal-to-evidence lineage. A bearish research decision can never be relabelled
as a short setup; a long_reclaim/range/missing setup is never short-eligible.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

SHORT_SETUP = "short_breakdown"
BEARISH = {"sell", "underweight"}


@dataclass
class LineageRow:
    symbol: str
    signal_at: str
    research_decision: str
    setup_type: str | None
    setup_symbol: str | None
    setup_at: str | None
    direction: str | None
    eligible: bool
    rejection: str
    next_stage: str
    automatic_short: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def audit(row: dict, *, setup: dict | None = None,
          signal_time: str | None = None) -> LineageRow:
    symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
    raw_decision = str(row.get("decision") or row.get("analyst_bias") or "").strip().lower()
    decision = {"bearish_research": "underweight", "long": "buy",
                "neutral": "hold"}.get(raw_decision, raw_decision)
    setup = setup or {}
    setup_symbol = str(setup.get("ticker") or setup.get("symbol") or "").strip().upper()
    setup_type = str(setup.get("setup") or "").strip().lower() or None
    setup_at = str(setup.get("generated_at") or setup.get("signal_at") or "") or None
    direction = str(setup.get("direction") or "").strip().lower() or None
    signal_at = str(signal_time or row.get("signal_at") or
                    row.get("decision_as_of") or row.get("generated_at") or "")

    if not symbol:
        rejection, next_stage = "missing_symbol", "none"
    elif not decision:
        rejection, next_stage = "missing_research_decision", "none"
    elif decision not in BEARISH:
        rejection, next_stage = "not_bearish_research", "none"
    elif not setup_symbol:
        rejection, next_stage = "setup_symbol_missing", "none"
    elif setup_symbol != symbol:
        rejection, next_stage = "symbol_identity_mismatch", "none"
    elif not setup_type:
        rejection, next_stage = "missing_point_in_time_setup", "none"
    elif setup_type != SHORT_SETUP:
        rejection, next_stage = f"setup_direction_mismatch:{setup_type}", "none"
    elif direction not in (None, "short"):
        rejection, next_stage = f"direction_mismatch:{direction}", "none"
    else:
        signal_dt, setup_dt = _parse(signal_at), _parse(setup_at)
        if not setup_dt:
            rejection, next_stage = "setup_timestamp_unavailable", "none"
        elif not signal_dt:
            rejection, next_stage = "signal_timestamp_missing", "none"
        elif setup_dt > signal_dt:
            rejection, next_stage = "setup_after_signal_future_data", "none"
        elif setup_dt > datetime.now(timezone.utc) + timedelta(minutes=5):
            rejection, next_stage = "setup_timestamp_in_future", "none"
        else:
            rejection, next_stage = "", "short_technical_validation"

    return LineageRow(
        symbol=symbol, signal_at=signal_at, research_decision=decision,
        setup_type=setup_type, setup_symbol=setup_symbol or None,
        setup_at=setup_at, direction=direction,
        eligible=not rejection, rejection=rejection, next_stage=next_stage)


def lineage_report(rows: list[dict], setups: dict[str, dict] | None = None,
                   *, note: str = "") -> dict:
    results = [audit(row, setup=(setups or {}).get(
        str(row.get("symbol") or row.get("ticker") or "").strip().upper()))
               for row in rows if isinstance(row, dict)]
    return {
        "track": "short_signal_lineage",
        "generated_at": datetime.now().astimezone().isoformat(),
        "rows": [r.as_dict() for r in results],
        "counts": {
            "total": len(results),
            "eligible": sum(r.eligible for r in results),
            "symbol_mismatch": sum(r.rejection in {"symbol_identity_mismatch",
                                                   "setup_symbol_missing"} for r in results),
            "setup_mismatch": sum(r.rejection.startswith("setup_direction_mismatch")
                                  for r in results),
            "future_data": sum(r.rejection == "setup_after_signal_future_data"
                               for r in results),
            "missing_setup": sum(r.rejection == "missing_point_in_time_setup"
                                 for r in results),
        },
        "shadow_only": True,
        "orders_submitted": 0,
        "entry_permission": "none - research lineage only",
        "note": note or ("A bearish research decision is never a short order. "
                         "Setups are never relabelled to gain eligibility."),
    }
