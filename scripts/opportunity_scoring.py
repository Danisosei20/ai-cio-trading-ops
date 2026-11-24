#!/usr/bin/env python3
"""Observational opportunity quality scoring (no execution effect).

Every factor must come from verified inputs. Missing data is scored zero for
that factor, never given a favourable default. The score cannot override a
deterministic risk rejection, change position size, or authorize an entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

FACTOR_WEIGHTS = {
    "setup_valid": 20,
    "trend_alignment": 15,
    "relative_strength": 15,
    "vwap_alignment": 15,
    "opening_range_confirmation": 10,
    "liquidity": 10,
    "reward_to_risk": 10,
    "regime_alignment": 5,
}


@dataclass
class ScoreResult:
    symbol: str
    direction: str
    score: float
    factors: dict[str, int]
    missing: list[str]
    evidence_complete: bool
    interpretation: str

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "direction": self.direction,
                "score": self.score, "factors": self.factors,
                "missing": self.missing, "evidence_complete": self.evidence_complete,
                "interpretation": self.interpretation}


def score_candidate(candidate: dict, *, factors: dict[str, bool | None]) -> ScoreResult:
    """Score only verified booleans; None means unknown and earns zero."""
    symbol = str(candidate.get("symbol", "")).upper()
    direction = str(candidate.get("direction", "")).lower()
    scores: dict[str, int] = {}
    missing: list[str] = []
    rejected = str(candidate.get("validation_status", "")) == "rejected"
    for name, weight in FACTOR_WEIGHTS.items():
        value = factors.get(name)
        if value is None:
            missing.append(name)
            scores[name] = 0
        else:
            scores[name] = 0 if rejected else (weight if bool(value) else 0)
    total = 0.0 if rejected else sum(scores.values())
    complete = not missing
    return ScoreResult(
        symbol=symbol, direction=direction, score=float(total), factors=scores,
        missing=missing, evidence_complete=complete,
        interpretation=("observational only - requires out-of-sample expectancy "
                        "validation; no size or authorization effect"))


def scoring_report(results: list[ScoreResult], *, sessions: int = 0,
                   expectancy: float | None = None,
                   note: str = "") -> dict:
    sufficient = sessions >= 5 and len(results) >= 50 and expectancy is not None
    return {
        "track": "observational_opportunity_scoring",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": [r.as_dict() for r in results],
        "evaluation": {
            "sessions": sessions, "candidates": len(results),
            "out_of_sample_sufficient": sufficient,
            "expectancy": expectancy if sufficient else None,
        },
        "shadow_only": True,
        "orders_submitted": 0,
        "entry_permission": "none - scoring never overrides risk",
        "note": note or ("Missing evidence scores zero. AI confidence cannot override "
                         "a deterministic risk rejection."),
    }
