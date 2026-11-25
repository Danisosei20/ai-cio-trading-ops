#!/usr/bin/env python3
"""Compare the live scout's setup rule with the canonical factory on same bars.

Observation only. No live routing is changed and no setup is relabelled to
gain eligibility. Discrepancies are classified, not silently reconciled.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from robinhood_tools import bar_validation


@dataclass(frozen=True)
class ComparisonRow:
    symbol: str
    scout_direction: str
    factory_direction: str
    scout_setup: str
    factory_setup: str
    classification: str
    reason: str

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "scout_direction": self.scout_direction,
                "factory_direction": self.factory_direction,
                "scout_setup": self.scout_setup, "factory_setup": self.factory_setup,
                "classification": self.classification, "reason": self.reason}


def scout_classify(*, price: float, vwap: float, or_high: float,
                   or_low: float, bars: int) -> tuple[str, str]:
    """Mirror of daytrade_scout.classify_direction + setup mapping."""
    if bars < 12:
        return "range", "range"
    if price > vwap and price >= or_low:
        return "long", "long_reclaim"
    if price < vwap and price <= or_high:
        return "short", "short_breakdown"
    return "range", "range"


def compare(*, symbol: str, price: float, vwap: float, or_high: float,
            or_low: float, bars: int, now: str | None = None,
            relative_to_spy: float | None = None) -> ComparisonRow:
    scout_dir, scout_setup = scout_classify(
        price=price, vwap=vwap, or_high=or_high, or_low=or_low, bars=bars)
    if scout_setup == "short_breakdown":
        canonical_ok, canonical_reason = bar_validation.short_breakdown(
            price, vwap=vwap, or_high=or_high, or_low=or_low,
                relative_to_spy=relative_to_spy)
        factory_dir = "short" if canonical_ok else "unknown"
        factory_setup = "short_breakdown" if canonical_ok else "rejected"
    elif scout_setup == "long_reclaim":
        canonical_ok, canonical_reason = bar_validation.long_reclaim(
            price, vwap=vwap, or_high=or_high, or_low=or_low,
                relative_to_spy=relative_to_spy)
        factory_dir = "long" if canonical_ok else "unknown"
        factory_setup = "long_reclaim" if canonical_ok else "rejected"
    else:
        factory_dir, factory_setup = "range", "range"
        canonical_ok, canonical_reason = False, "not a tradeable setup"

    if scout_setup == factory_setup:
        classification, reason = "agreement", "both rules agree"
    elif (canonical_reason in {"price not above opening-range high",
                               "price not below opening-range low"} and
          scout_setup in {"long_reclaim", "short_breakdown"}):
        # Documented definitional difference: scout uses the opposite OR
        # boundary as a confluence filter. Both rules implement their own
        # specification; this is not a coding defect.
        classification = "intentional_strategy_difference"
        reason = (f"canonical requires the stronger opening-range boundary: "
                  f"{canonical_reason}; scout uses the looser boundary")
    elif scout_setup in {"short_breakdown", "long_reclaim"} and not canonical_ok:
        classification = "implementation_defect"
        reason = f"canonical rule rejects for an unexplained reason: {canonical_reason}"
    elif scout_setup == "range" and factory_setup != "range":
        classification = "implementation_defect"
        reason = "canonical rule accepts a setup the live scout labels range"
    else:
        classification, reason = "intentional_strategy_difference", "unclassified difference"
    return ComparisonRow(symbol=symbol, scout_direction=scout_dir,
                         factory_direction=factory_dir, scout_setup=scout_setup,
                         factory_setup=factory_setup, classification=classification,
                         reason=reason)


def comparison_report(rows: list[ComparisonRow], *, note: str = "") -> dict:
    return {
        "track": "scout_vs_factory",
        "canonical_spec_version": "intraday-setups/v1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": [r.as_dict() for r in rows],
        "counts": {
            "agreement": sum(r.classification == "agreement" for r in rows),
            "implementation_defect": sum(
                r.classification == "implementation_defect" for r in rows),
            "intentional_difference": sum(
                r.classification == "intentional_strategy_difference" for r in rows),
        },
        "scope": ("setup-rule comparison only; excludes bar validation, freshness, "
                  "odds and risk gates; source snapshots are post-close and "
                  "must not be represented as live-eligible signals"),
        "shadow_only": True,
        "orders_submitted": 0,
        "entry_permission": "none - comparison only",
        "note": note or ("Live scout routing is unchanged. Any canonical promotion "
                         "requires replay, out-of-sample evidence and owner approval."),
    }
