#!/usr/bin/env python3
"""Canonical, point-in-time intraday signal records (observation only).

Creates a stable schema for long and short candidates with explicit identity,
timestamps, evidence references and validation status. It never places orders,
never derives authorization and never invents prices/stops/targets when the
required evidence is absent. A bearish research verdict remains research.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from robinhood_tools import bar_validation

STRATEGY_VERSION = "phase8.4-intraday-signals-v1"
LONG = "long_reclaim"
SHORT = "short_breakdown"


@dataclass
class SignalCandidate:
    signal_id: str
    symbol: str
    direction: str
    setup: str
    timeframe: str
    signal_at: str
    bar_as_of: str
    source: str
    feed: str
    volume_basis: str
    entry_trigger: str
    invalidation_level: float | None
    stop_method: str
    target_method: str
    planned_rr: float | None
    freshness: str
    evidence_refs: list[str] = field(default_factory=list)
    validation_status: str = "rejected"
    rejection_reason: str = ""
    research_link: str | None = None
    evidence_hash: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _utc(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return datetime.now(timezone.utc).isoformat()


def make_candidate(*, symbol: str, bars: list[dict], setup: str,
                   timeframe: str = "5m", source: str = "yfinance",
                   feed: str = "iex", relative_to_spy: float | None = None,
                   now: str | None = None,
                   evidence_refs: list[str] | None = None) -> SignalCandidate:
    """Validate evidence first; only then classify a directional setup."""
    symbol = str(symbol).strip().upper()
    stamp = now or datetime.now(timezone.utc).isoformat()
    validated = bar_validation.validate_bars(bars, feed=feed, now=_parse_utc(now))
    as_of = ""
    if validated.bars:
        as_of = validated.bars[-1]["t"].isoformat()
    refs = list(evidence_refs or [])
    digest = hashlib.sha256(json.dumps(
        {"symbol": symbol, "bars": bars, "setup": setup, "as_of": as_of,
         "timeframe": timeframe, "source": source, "feed": feed,
         "relative_to_spy": relative_to_spy,
         "evidence_refs": refs, "strategy_version": STRATEGY_VERSION},
        sort_keys=True, default=str).encode()).hexdigest()

    def base(direction: str, trigger: str, invalidation: float | None,
             status: str, reason: str) -> SignalCandidate:
        return SignalCandidate(
            signal_id=f"{symbol}:{setup}:{as_of or stamp}:{digest[:10]}",
            symbol=symbol, direction=direction, setup=setup,
            timeframe=timeframe, signal_at=stamp, bar_as_of=as_of,
            source=source, feed=feed,
            volume_basis=validated.volume_basis, entry_trigger=trigger,
            invalidation_level=invalidation,
            stop_method="opening-range high (short) / low (long)" if invalidation else "unavailable",
            target_method="2R from entry and validated stop" if invalidation else "unavailable",
            planned_rr=2.0 if invalidation else None,
            freshness=validated.freshness, evidence_refs=refs,
            validation_status=status, rejection_reason=reason,
            evidence_hash=digest)

    if validated.status != "ok":
        return base("unknown", "none", None, "rejected", validated.reason)
    last = validated.bars[-1]
    price = last["close"]
    opening = validated.opening_range or {}
    if setup == SHORT:
        ok, reason = bar_validation.short_breakdown(
            price, vwap=validated.vwap, or_high=opening["high"],
            or_low=opening["low"], relative_to_spy=relative_to_spy)
        direction, invalidation = "short", opening["high"]
    elif setup == LONG:
        ok, reason = bar_validation.long_reclaim(
            price, vwap=validated.vwap, or_high=opening["high"],
            or_low=opening["low"], relative_to_spy=relative_to_spy)
        direction, invalidation = "long", opening["low"]
    else:
        return base("unknown", "none", None, "rejected",
                    f"unsupported setup {setup!r}")
    if not ok:
        return base(direction, "none", None, "rejected", reason)
    return base(direction, f"{setup} confirmed at {price}", invalidation,
                "validated", "")


def _parse_utc(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def candidate_report(candidates: list[SignalCandidate], *, note: str = "") -> dict:
    return {
        "track": "intraday_signal_factory",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": STRATEGY_VERSION,
        "candidates": [c.as_dict() for c in candidates],
        "counts": {
            "total": len(candidates),
            "valid": sum(c.validation_status == "validated" for c in candidates),
            "long": sum(c.direction == "long" and c.validation_status == "validated"
                        for c in candidates),
            "short": sum(c.direction == "short" and c.validation_status == "validated"
                         for c in candidates),
            "rejected": sum(c.validation_status == "rejected" for c in candidates),
        },
        "shadow_only": True,
        "orders_submitted": 0,
        "entry_permission": "none - technical observation only",
        "note": note or ("Signals are research evidence. Deterministic risk gates and "
                         "authorization remain authoritative."),
    }
