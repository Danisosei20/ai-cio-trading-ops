#!/usr/bin/env python3
"""Provenance for persisted market-data evidence (read-only).

Every artifact must state source, retrieval time, market as-of, cache status,
freshness, timeframe and instrument identity. IEX-only volume is explicitly
labelled and never presented as consolidated market volume. Missing required
fields keep execution fail-closed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


class EvidenceIncomplete(ValueError):
    pass


@dataclass(frozen=True)
class MarketDataEvidence:
    instrument: str
    source: str
    feed: str
    volume_basis: str
    timeframe: str
    retrieved_at: str
    market_as_of: str
    cache_status: str
    freshness: str
    data_version: str = "v1"
    value: Any = None

    def as_dict(self) -> dict:
        return asdict(self)


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceIncomplete("missing timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceIncomplete("malformed timestamp") from exc
    if parsed.tzinfo is None:
        raise EvidenceIncomplete("timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def build_evidence(*, instrument: str, source: str, feed: str,
                   timeframe: str, retrieved_at: str, market_as_of: str,
                   cache_status: str = "fresh_fetch", value: Any = None,
                   max_age_minutes: float = 10.0,
                   now: str | None = None) -> MarketDataEvidence:
    """Construct evidence with a freshly COMPUTED freshness label."""
    retrieved = _utc(retrieved_at)
    as_of = _utc(market_as_of)
    current = _utc(now) if now else datetime.now(timezone.utc)
    age = current - as_of
    if as_of > retrieved or age < timedelta(0):
        freshness = "unverified"
    elif age <= timedelta(minutes=max_age_minutes):
        freshness = "fresh"
    else:
        freshness = "stale"
    basis = "iex_only" if "iex" in feed.lower() else "consolidated_or_unspecified"
    return MarketDataEvidence(
        instrument=str(instrument).upper(), source=str(source), feed=str(feed),
        volume_basis=basis, timeframe=str(timeframe), retrieved_at=retrieved_at,
        market_as_of=market_as_of, cache_status=cache_status, freshness=freshness,
        value=value)


def validate_evidence(evidence: MarketDataEvidence, *, now: str | None = None,
                      max_age_minutes: float = 10.0) -> MarketDataEvidence:
    """Block execution on missing/stale provenance; never trust a stored label."""
    for field in ("instrument", "source", "feed", "timeframe", "market_as_of",
                  "retrieved_at", "cache_status", "freshness"):
        if not str(getattr(evidence, field, "") or "").strip():
            raise EvidenceIncomplete(f"missing market-data field: {field}")
    if evidence.cache_status not in {"fresh_fetch", "cache_hit"}:
        raise EvidenceIncomplete(f"unknown cache status: {evidence.cache_status}")
    retrieved = _utc(evidence.retrieved_at)
    as_of = _utc(evidence.market_as_of)
    current = _utc(now) if now else datetime.now(timezone.utc)
    if as_of > retrieved or retrieved > current:
        raise EvidenceIncomplete("impossible market-data timestamps")
    if current - as_of > timedelta(minutes=max_age_minutes):
        raise EvidenceIncomplete("market data older than freshness threshold")
    return evidence


def describe_feed(evidence: MarketDataEvidence) -> str:
    """Human-readable, non-misleading feed description for reports/UI."""
    if evidence.volume_basis == "iex_only":
        return (f"{evidence.feed} (IEX-only volume; NOT consolidated market volume) "
                f"as of {evidence.market_as_of}")
    return f"{evidence.feed} ({evidence.volume_basis}) as of {evidence.market_as_of}"
