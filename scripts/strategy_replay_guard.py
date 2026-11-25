#!/usr/bin/env python3
"""Point-in-time replay integrity guard for immutable market snapshots.

Rejects post-close, duplicated or unverifiable evidence before it can be used
to claim walk-forward or out-of-sample strategy results.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path


def bars_digest(snapshot: dict) -> str:
    bars = snapshot.get("bars") or []
    return hashlib.sha256(json.dumps(
        bars, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str).encode("utf-8")).hexdigest()


def usable(snapshot: dict, *, as_of: str) -> tuple[bool, str]:
    """Return (usable, reason) for a decision made at ``as_of``."""
    freshness = str(snapshot.get("freshness", "")).lower()
    if freshness not in {"fresh", "verified", "live"}:
        return False, f"snapshot freshness {freshness!r} is not point-in-time"
    bars = snapshot.get("bars") or []
    if not bars:
        return False, "snapshot has no bars"
    last = bars[-1]
    stamp = str(last.get("t") or last.get("time") or "")
    if not stamp:
        return False, "last bar has no timestamp"
    try:
        bar_time, decision_time = datetime.fromisoformat(stamp), datetime.fromisoformat(as_of)
    except ValueError:
        return False, "unparseable bar or decision timestamp"
    if bar_time.astimezone() > decision_time.astimezone():
        return False, "snapshot contains a bar after the decision time (future leakage)"
    if str(snapshot.get("data_completeness", "")).lower() not in {"complete", "verified"}:
        return False, "snapshot completeness not verified"
    return True, "point-in-time evidence"


def audit_set(root: Path, date: str) -> dict:
    """Report duplicate bar sets across cycle labels for one session."""
    seen: dict[str, list[str]] = {}
    for path in sorted((root / "outputs/market_snapshots" / date).glob("**/snapshot.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        seen.setdefault(bars_digest(doc), []).append(str(path.parent.name))
    duplicates = {digest: cycles for digest, cycles in seen.items()
                  if len(cycles) > 1}
    return {"date": date, "snapshots": len(seen), "unique_bar_sets": len(seen),
            "duplicate_groups": duplicates,
            "verdict": "NOT VERIFIED for OOS" if duplicates else "no duplicate bar sets"}
