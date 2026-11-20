"""Single source of truth for the paper desk's active operating contract."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DeskPolicy:
    version: str = "paper-options-2026-11-20-v1"
    paper_only: bool = True
    quoted_iv_cap: float = 0.45
    guard_stop_pct: float = 0.25
    modeled_reward_pct: float = 0.50
    minimum_reward_risk: float = 2.0
    max_vix: float = 25.0
    unlimited_winners: bool = True
    spy_long_term_confirmation: bool = True


POLICY = DeskPolicy()
FRESHNESS_MINUTES = {"market": 5, "broker": 5, "guard": 24 * 60,
                     "report": 24 * 60, "fleet": 30}


def _paths() -> dict[str, str]:
    return {
        "database": str(ROOT / "outputs" / "paper" / "cio.db"),
        "options_guard": str(ROOT / "outputs" / "paper" / "options_guard.json"),
        "equity_guard": str(ROOT / "outputs" / "paper" / "guard.json"),
        "paper_report": str(ROOT / "outputs" / "paper" / "paper_session_report.json"),
    }


def policy_snapshot() -> dict:
    """Return the contract in a JSON-safe shape for bots, reports, and UI."""
    return {
        "policy_version": POLICY.version,
        "paper_only": POLICY.paper_only,
        "options": {
            "quoted_iv_cap": POLICY.quoted_iv_cap,
            "guard_stop_pct": POLICY.guard_stop_pct,
            "modeled_reward_pct": POLICY.modeled_reward_pct,
            "minimum_reward_risk": POLICY.minimum_reward_risk,
            "unlimited_winners": POLICY.unlimited_winners,
            "max_vix": POLICY.max_vix,
            "spy_long_term_confirmation": POLICY.spy_long_term_confirmation,
        },
        "freshness_minutes": dict(FRESHNESS_MINUTES),
        "paths": _paths(),
    }


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def policy_status(*, paper_safe: bool, source_times: dict[str, str] | None = None,
                  now: datetime | None = None) -> dict:
    """Classify runtime safety without contacting a broker or mutating state."""
    reasons: list[str] = []
    stale_sources: list[str] = []
    if not paper_safe:
        reasons.append("runtime is not paper-safe")
    current = now or datetime.now(timezone.utc)
    for name, timestamp in (source_times or {}).items():
        parsed = _parse_time(timestamp)
        limit = FRESHNESS_MINUTES.get(name)
        if parsed is None or limit is None or (current - parsed).total_seconds() > limit * 60:
            stale_sources.append(name)
    if stale_sources:
        reasons.append("stale sources: " + ", ".join(sorted(stale_sources)))
    status = "BLOCKED" if not paper_safe else ("DEGRADED" if stale_sources else "READY")
    return {"status": status, "policy_version": POLICY.version,
            "paper_only": POLICY.paper_only, "reasons": reasons,
            "stale_sources": sorted(stale_sources)}
