"""Read-only, fail-closed safety status for the Nexus office viewer.

Assembles one small, sanitized payload describing the desk's posture:
entries paused, paper-only mode, account flatness, last certification,
fill reconciliation, protective schedulers, and alert verification.

Every source is best-effort: an unreadable value yields UNKNOWN or
NOT VERIFIED, never a reassuring default. No secrets are emitted.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")

# Only these flag values are emitted. Keys/tokens never leave this module.
_ALLOWED_ENV_KEYS = ("TRADING_ENABLED", "PAPER_TRADING_ENABLED", "TRADING_MODE")

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


def read_env(path: Path) -> dict:
    """Read only the three safety flags from a KEY=VALUE .env file."""
    values: dict[str, str] = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            name = key.strip()
            if name in _ALLOWED_ENV_KEYS:
                values[name] = value.strip().strip('"').strip("'")
    except (OSError, ValueError, UnicodeError):
        pass
    return values


def _read_json(path: Path) -> dict | None:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return doc if isinstance(doc, dict) else None


_TASK_CACHE: dict[str, tuple[float, str | None]] = {}
_TASK_TTL = 30.0


def task_state(name: str, *, now: float | None = None) -> str | None:
    """Cached read of a Windows scheduled task's state, or None on failure.

    Cached for 30s so the 2s office poll never spawns nine schtasks calls
    per cycle. Both successes and failures are cached.
    """
    current = time.monotonic() if now is None else now
    cached = _TASK_CACHE.get(name)
    if cached is not None and current - cached[0] < _TASK_TTL:
        return cached[1]
    state: str | None = None
    try:
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"],
            capture_output=True, text=True, timeout=15)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.strip().startswith("Scheduled Task State:"):
                    state = line.split(":", 1)[1].strip() or None
                    break
    except (OSError, subprocess.SubprocessError):
        state = None
    _TASK_CACHE[name] = (current, state)
    return state


def account_state(env: dict) -> str:
    """Read broker positions through the paper-only transport, fail closed."""
    try:
        from robinhood_tools.alpaca_paper import (
            AlpacaPaperBackend,
            AlpacaPaperHttpTransport,
        )

        merged = {**env, **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(merged))
        return "FLAT" if not backend.list_positions() else "POSITIONS OPEN"
    except Exception:  # noqa: BLE001 - an unreadable book is not an empty one
        return "UNKNOWN"


def snapshot(root: Path, *, env: dict | None = None, query=None,
             account_probe=None) -> dict:
    """Assemble the full fail-closed safety payload for the office viewer."""
    root = Path(root)
    if env is None:
        env = read_env(root / ".env")
    query = query or task_state
    account_probe = account_probe or account_state

    cert = classify_certification(
        _read_json(root / "outputs/certification/paper_certification.json"))
    recon = classify_reconciliation(
        _read_json(root / "outputs/paper/fill_reconciliation.json"))
    protective_states = {name: query(name) for name in PROTECTIVE_TASKS}
    protective_ok = all(state in _ENABLED_STATES
                        for state in protective_states.values())

    return {
        "entries": classify_entries({name: query(name) for name in ENTRY_TASKS}),
        "mode": classify_mode(env),
        "account": account_probe(),
        "certification": cert,
        "reconciliation": recon,
        "protective": {
            "status": "ENABLED" if protective_ok else "UNKNOWN",
            "tasks": list(PROTECTIVE_TASKS),
        },
        "alerts": "NOT VERIFIED",
        "generated_at": datetime.now(ET).isoformat(),
    }
