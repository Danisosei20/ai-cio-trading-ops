#!/usr/bin/env python3
"""Point-in-time bar validation for technical setups (read-only).

Completed bars only, ordered, deduplicated, gap-aware. VWAP and opening range
use exactly the validated inputs. IEX volume is labelled as IEX-only; no
consolidated breadth inference is allowed from it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class BarValidation:
    status: str
    reason: str
    bars: list[dict] | None = None
    opening_range: dict | None = None
    vwap: float | None = None
    volume_basis: str = "unknown"
    gaps: list[str] | None = None
    freshness: str = "unknown"

    def as_dict(self) -> dict:
        return asdict(self)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def validate_bars(bars: list[dict], *, interval_minutes: int = 5,
                  min_bars: int = 12, opening_bars: int = 6,
                  feed: str = "iex", session_end: str | None = None,
                  now: datetime | None = None) -> BarValidation:
    """Fail closed on malformed, duplicated, incomplete or stale bars."""
    supplied = list(bars or [])
    if any(not isinstance(b, dict) for b in supplied):
        return BarValidation("rejected", "non-dict bar in input")
    rows = supplied
    if len(rows) < min_bars:
        return BarValidation("rejected", f"insufficient bars: {len(rows)} < {min_bars}")
    stamps: list[datetime] = []
    cleaned: list[dict] = []
    for row in rows:
        ts = _timestamp(row.get("t") or row.get("timestamp") or row.get("datetime"))
        if ts is None:
            return BarValidation("rejected", "bar lacks a valid timestamp")
        try:
            close = float(row["c"] if "c" in row else row["close"])
            high = float(row["h"] if "h" in row else row["high"])
            low = float(row["l"] if "l" in row else row["low"])
            volume = float(row.get("v", row.get("volume", 0)) or 0)
        except (KeyError, TypeError, ValueError):
            return BarValidation("rejected", "bar has malformed OHLCV fields")
        if close <= 0 or high < low or volume < 0 or close > high or close < low:
            return BarValidation("rejected", "bar has impossible OHLCV values")
        stamps.append(ts)
        cleaned.append({"t": ts, "close": close, "high": high,
                        "low": low, "volume": volume})
    stamps_before = [r["t"] for r in cleaned]
    if stamps_before != sorted(stamps_before):
        return BarValidation("rejected", "bars out of chronological order")
    cleaned.sort(key=lambda r: r["t"])
    stamps = [r["t"] for r in cleaned]
    duplicates = [str(ts) for i, ts in enumerate(stamps)
                  if i and ts == stamps[i - 1]]
    if duplicates:
        return BarValidation("rejected", f"duplicate bars: {duplicates[:3]}")

    gaps = []
    for earlier, later in zip(stamps, stamps[1:]):
        delta = later - earlier
        if delta != timedelta(minutes=interval_minutes):
            gaps.append(f"{earlier.isoformat()}->{later.isoformat()}")

    current = now or datetime.now(timezone.utc)
    if stamps[-1] > current + timedelta(seconds=30):
        return BarValidation("rejected", "future-dated bar (look-ahead)")
    if session_end:
        end = _timestamp(session_end)
        if end is None:
            return BarValidation("rejected", "invalid session end timestamp")
        if stamps[-1] > end:
            return BarValidation("rejected", "bar after session end")
        if stamps[-1] < end - timedelta(minutes=interval_minutes):
            return BarValidation("rejected", "last bar is not a completed session bar")
    if gaps:
        return BarValidation("rejected", "missing bars in series", gaps=gaps)
    if current - stamps[-1] > timedelta(minutes=max(15, 4 * interval_minutes)):
        return BarValidation("rejected", "bars are stale for this decision")

    opening = cleaned[:opening_bars]
    pv = sum(((r["high"] + r["low"] + r["close"]) / 3) * r["volume"] for r in cleaned)
    volume = sum(r["volume"] for r in cleaned)
    vwap = round(pv / volume, 6) if volume > 0 else None
    if vwap is None:
        return BarValidation("rejected", "VWAP cannot be computed without volume")
    basis = "iex_only" if str(feed).lower() == "iex" else "consolidated_or_unspecified"
    return BarValidation(
        "ok", "valid point-in-time bars", bars=cleaned,
        opening_range={"high": max(r["high"] for r in opening),
                       "low": min(r["low"] for r in opening),
                       "bars": len(opening)},
        vwap=vwap, volume_basis=basis, gaps=gaps, freshness="fresh")


def long_reclaim(price: float, *, vwap: float, or_high: float,
                 or_low: float, relative_to_spy: float | None = None,
                 min_relative_strength: float = 0.0) -> tuple[bool, str]:
    """Independent bullish setup: above VWAP and above the opening-range high."""
    if price <= vwap:
        return False, "price not above VWAP"
    if price <= or_high:
        return False, "price not above opening-range high"
    if price < or_low:
        return False, "price below opening-range low"
    if relative_to_spy is not None and relative_to_spy < min_relative_strength:
        return False, "relative strength below required threshold"
    return True, "valid long_reclaim"


def short_breakdown(price: float, *, vwap: float, or_high: float,
                    or_low: float, relative_to_spy: float | None = None,
                    min_relative_weakness: float = 0.0) -> tuple[bool, str]:
    """Independent bearish setup: below VWAP and below the opening-range low."""
    if price >= vwap:
        return False, "price not below VWAP"
    if price >= or_low:
        return False, "price not below opening-range low"
    if price > or_high:
        return False, "price above opening-range high"
    if relative_to_spy is not None and relative_to_spy < min_relative_weakness:
        return False, "relative weakness below required threshold"
    return True, "valid short_breakdown"
