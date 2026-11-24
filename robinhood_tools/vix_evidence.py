#!/usr/bin/env python3
"""Timestamped, validated VIX evidence for option-entry policy checks.

The existing ``current_vix`` returns a bare float from a daily yfinance bar
and discards its index, so the two calls in one decision can use different
values with no as-of evidence. This module keeps provenance. It does NOT alter
the 25.0 threshold or the existing gate callers.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

ET_TZ = "America/New_York"
SOURCE = "yfinance:^VIX"
INSTRUMENT = "^VIX"


@dataclass(frozen=True)
class VixEvidence:
    value: float
    source: str
    instrument: str
    retrieved_at: str          # UTC, when this process fetched the data
    market_as_of: str          # UTC, the bar's exchange timestamp
    market_as_of_source: str   # "bar_index" or "unavailable"
    timezone: str
    freshness: str             # "fresh" | "stale" | "unverified"
    cache_status: str          # "fresh_fetch" | "cache_hit" | "unknown"
    policy_threshold: float
    data_version: str = "v1"

    def as_dict(self) -> dict:
        return asdict(self)


class VixEvidenceError(ValueError):
    pass


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise VixEvidenceError("missing UTC timestamp")
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise VixEvidenceError("timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def build_evidence(frame, *, retrieved_at: str | None = None,
                   max_age_hours: float = 36.0,
                   threshold: float | None = None) -> VixEvidence:
    """Derive evidence from a yfinance-style frame, preserving the bar index.

    If the frame has no usable timestamp, ``market_as_of`` is empty and
    freshness is ``unverified``. Such evidence is NEVER accepted as fresh.
    """
    from robinhood_tools.desk_policy import DeskPolicy  # noqa: PLC0415

    retrieved = retrieved_at or datetime.now(timezone.utc).isoformat()
    try:
        closes = frame["Close"].dropna()
    except Exception as exc:  # noqa: BLE001
        raise VixEvidenceError(f"malformed VIX frame: {exc}") from exc
    if len(closes) == 0:
        raise VixEvidenceError("empty VIX frame")

    try:
        value = float(closes.iloc[-1])
    except (TypeError, ValueError) as exc:
        raise VixEvidenceError("malformed VIX value") from exc
    if not math.isfinite(value) or value <= 0:
        raise VixEvidenceError("impossible VIX value")

    as_of = ""
    as_of_source = "unavailable"
    try:
        idx = closes.index[-1]
        if hasattr(idx, "isoformat"):
            text = idx.isoformat()
            # yfinance ^VIX index is exchange-local; attach ET, then convert.
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfoCompat())
            as_of = parsed.astimezone(timezone.utc).isoformat()
            as_of_source = "bar_index"
    except Exception:  # noqa: BLE001 - no fabricated timestamps
        as_of = ""
        as_of_source = "unavailable"

    freshness = "unverified"
    if as_of:
        try:
            age = _parse_utc(retrieved) - _parse_utc(as_of)
            if timedelta(0) <= age <= timedelta(hours=max_age_hours):
                freshness = "fresh"
            elif age > timedelta(hours=max_age_hours):
                freshness = "stale"
            else:
                freshness = "unverified"  # bar timestamp in the future
        except VixEvidenceError:
            freshness = "unverified"

    return VixEvidence(
        value=round(value, 4), source=SOURCE, instrument=INSTRUMENT,
        retrieved_at=retrieved, market_as_of=as_of,
        market_as_of_source=as_of_source, timezone=ET_TZ, freshness=freshness,
        cache_status="fresh_fetch",
        policy_threshold=float(threshold if threshold is not None else DeskPolicy().max_vix),
    )


def ZoneInfoCompat():
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    return ZoneInfo(ET_TZ)


def validate_evidence(evidence: VixEvidence, *, now: str | None = None,
                      max_age_hours: float = 36.0,
                      max_retrieval_age_hours: float = 6.0) -> VixEvidence:
    """Reject values without provenance, stale or impossible timing.

    Freshness is recomputed from timestamps on every validation; a
    caller-supplied ``freshness`` label is never trusted.
    """
    if not evidence.source or not evidence.instrument:
        raise VixEvidenceError("missing source or instrument identity")
    if evidence.instrument != INSTRUMENT:
        raise VixEvidenceError("unsupported VIX instrument")
    if not math.isfinite(evidence.value) or evidence.value <= 0:
        raise VixEvidenceError("malformed VIX value")
    if evidence.market_as_of_source != "bar_index" or not evidence.market_as_of:
        raise VixEvidenceError("missing market as-of evidence")
    try:
        retrieved = _parse_utc(evidence.retrieved_at)
        as_of = _parse_utc(evidence.market_as_of)
        current = _parse_utc(now) if now else datetime.now(timezone.utc)
    except VixEvidenceError as exc:
        raise VixEvidenceError(f"invalid timestamp: {exc}") from exc
    if as_of > retrieved or retrieved > current + timedelta(minutes=5):
        raise VixEvidenceError("impossible market/retrieval timestamp")
    age = retrieved - as_of
    if age < timedelta(0) or age > timedelta(hours=max_age_hours):
        raise VixEvidenceError(f"VIX evidence stale or impossible: age={age}")
    if current - retrieved > timedelta(hours=max_retrieval_age_hours):
        raise VixEvidenceError("VIX retrieval too old for this session")
    if evidence.policy_threshold <= 0:
        raise VixEvidenceError("missing policy threshold")
    return evidence
