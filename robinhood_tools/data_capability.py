#!/usr/bin/env python3
"""Deterministic market-data capability classification (read-only).

Maps strategy data requirements to observed entitlements. Unsupported or
stale capabilities never authorize entries. IEX-only volume is never presented
as consolidated market volume; delayed SIP is never substituted for live
execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

SUPPORTED = "SUPPORTED"
LIMITED = "LIMITED"
UNSUPPORTED = "UNSUPPORTED"
STALE = "STALE"
UNAVAILABLE = "UNAVAILABLE"

CAPABILITIES = (
    "realtime_iex", "realtime_sip", "historical_bars", "delayed_sip",
    "realtime_quotes", "consolidated_volume", "consolidated_breadth",
    "options_quotes", "short_eligibility",
)

# Strategy -> required capabilities. These are policy declarations, not trades.
STRATEGY_REQUIREMENTS = {
    "daytrade_long": ("realtime_iex", "historical_bars", "realtime_quotes"),
    "daytrade_short": ("realtime_iex", "historical_bars", "short_eligibility",
                       "realtime_quotes"),
    "relative_strength": ("realtime_iex", "historical_bars"),
    "consolidated_breadth": ("realtime_sip", "consolidated_breadth"),
    "options_entry": ("options_quotes", "realtime_quotes"),
    "overnight_swing": ("historical_bars", "realtime_iex"),
}


@dataclass(frozen=True)
class CapabilitySnapshot:
    realtime_iex: str = SUPPORTED
    realtime_sip: str = UNSUPPORTED
    historical_bars: str = SUPPORTED
    delayed_sip: str = LIMITED
    realtime_quotes: str = SUPPORTED
    consolidated_volume: str = UNAVAILABLE
    consolidated_breadth: str = UNSUPPORTED
    options_quotes: str = SUPPORTED
    short_eligibility: str = SUPPORTED
    feed: str = "iex"
    volume_basis: str = "iex_only"
    as_of: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def classify_strategy(strategy: str, snapshot: CapabilitySnapshot) -> dict:
    required = STRATEGY_REQUIREMENTS.get(strategy)
    if required is None:
        return {"strategy": strategy, "status": UNSUPPORTED,
                "reason": "unknown strategy requirements", "required": []}
    states = {name: getattr(snapshot, name, UNAVAILABLE) for name in required}
    if any(value in {UNSUPPORTED, UNAVAILABLE, STALE} for value in states.values()):
        status = UNSUPPORTED
    elif any(value == LIMITED for value in states.values()):
        status = LIMITED
    else:
        status = SUPPORTED
    entry_allowed = status == SUPPORTED and snapshot.feed == "iex"
    return {
        "strategy": strategy, "status": status, "required": list(required),
        "states": states, "entry_capability_ok": entry_allowed,
        "snapshot_provenance": "caller-supplied; must be sourced from live diagnostics",
        "reason": ("all required capabilities supported" if status == SUPPORTED
                   else "missing/stale capabilities: " +
                        ", ".join(k for k, v in states.items()
                                  if v != SUPPORTED)),
        "note": ("IEX-only volume is not consolidated volume; delayed data is never "
                 "used to authorize a live entry"),
    }


def capability_report(snapshot: CapabilitySnapshot) -> dict:
    strategies = {name: classify_strategy(name, snapshot)
                  for name in STRATEGY_REQUIREMENTS}
    return {
        "track": "market_data_capability",
        "snapshot": snapshot.as_dict(),
        "strategies": strategies,
        "requires_sip": sorted(name for name, row in strategies.items()
                               if "realtime_sip" in row["required"] and
                               row["status"] == UNSUPPORTED),
        "orders_submitted": 0,
        "entry_permission": "none - capability classification only",
        "note": "No paid subscription activated; no SIP endpoint retried by this layer.",
    }
