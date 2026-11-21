"""Read-only, fail-closed safety status for the Nexus office viewer.

Assembles one small, sanitized payload describing the desk's posture:
entries paused, paper-only mode, account flatness, last certification,
fill reconciliation, protective schedulers, and alert verification.

Every source is best-effort: an unreadable value yields UNKNOWN or
NOT VERIFIED, never a reassuring default. No secrets are emitted.
"""
from __future__ import annotations

# The nine registered desk tasks, classified by what they do.
# Entry tasks drive the ENTRIES PAUSED badge; protective tasks are the
# independent exits/flattening/watchdog that stay enabled while entries are
# paused. Neutral tasks are reporting only.
ENTRY_TASKS = (
    "AI-CIO-AutoTrade-0945",
    "AI-Desk-OptionsWatch-1510",
    "AI-Desk-IntradayLoop",
    "AI-Desk-OvernightEnter",
)
PROTECTIVE_TASKS = (
    "AI-Desk-Flatten-1545",
    "AI-Desk-OvernightExit",
    "AI-Desk-Watchdog-0950",
)
NEUTRAL_TASKS = (
    "AI-Desk-ScoreShadows-1630",
    "AI-Desk-WeeklyReview-1645",
)

_ENABLED_STATES = ("Enabled", "Ready", "Running")
_KNOWN_STATES = ("Disabled",) + _ENABLED_STATES


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() == "true"


def classify_mode(env: dict) -> str:
    """Map desk flags to a single, safety-first label.

    Checked in order: the live kill switch wins over everything, then an
    explicit live-approval/research-only mode, then paper_auto with the
    autonomous paper flag. Anything unreadable is UNKNOWN.
    """
    if _truthy(env.get("TRADING_ENABLED")):
        return "LIVE"
    mode = str(env.get("TRADING_MODE") or "").strip().lower()
    if mode == "live_approval":
        return "LIVE APPROVAL"
    if mode == "research_only":
        return "RESEARCH ONLY"
    if mode == "paper_auto" and _truthy(env.get("PAPER_TRADING_ENABLED")):
        return "PAPER ONLY"
    return "UNKNOWN"


def classify_entries(states: dict[str, str | None]) -> str:
    """PAUSED only when all four entry tasks are explicitly Disabled.

    Any missing/unqueryable/unrecognised task state fails closed to
    UNKNOWN. One enabled entry task makes the badge ACTIVE.
    """
    values = [states.get(name) for name in ENTRY_TASKS]
    if any(value not in _KNOWN_STATES for value in values):
        return "UNKNOWN"
    if all(value == "Disabled" for value in values):
        return "PAUSED"
    return "ACTIVE"


def classify_certification(doc: dict | None) -> dict:
    """Last offline/broker certification verdict, or NOT VERIFIED."""
    if not isinstance(doc, dict):
        return {"status": "NOT VERIFIED", "generated_at": "", "label": "NOT VERIFIED"}
    status = "PASS" if str(doc.get("verdict") or "").upper() == "PASS" else "FAIL"
    generated = str(doc.get("generated_at") or "")
    label = f"{status} @ {generated}" if generated else status
    return {"status": status, "generated_at": generated, "label": label}


def classify_reconciliation(doc: dict | None) -> dict:
    """Broker-fill reconciliation verdict and orphan count."""
    if not isinstance(doc, dict):
        return {"status": "never run", "orphans": 0}
    orphans = doc.get("orphan_orders") or []
    count = len(orphans) if isinstance(orphans, list) else 0
    return {"status": str(doc.get("status") or "unknown"), "orphans": count}
