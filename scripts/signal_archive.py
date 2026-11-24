#!/usr/bin/env python3
"""Append-only point-in-time signal archive with integrity manifests.

Never overwrites earlier evidence. Stores source bars, technical values,
classification, strategy version, rejection reasons, research linkage and
an evidence hash. Future-dated bars are rejected by bar_validation before
archival. Replay reads only archived observations; no later bars are used.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from robinhood_tools.bar_validation import validate_bars


def _digest(doc: dict) -> str:
    return hashlib.sha256(json.dumps(
        doc, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def archive_signal(root: Path, candidate: dict, *, bars: list[dict],
                   strategy_version: str, now: str | None = None,
                   expected_hash: str | None = None) -> Path | None:
    """Append one immutable, hash-chained signal observation."""
    stamp = now or datetime.now(timezone.utc).isoformat()
    if expected_hash and candidate.get("evidence_hash") not in (None, expected_hash):
        return None  # caller's signal no longer matches the factory evidence
    validated = validate_bars(bars, now=_parse(stamp))
    record = {
        "signal": candidate,
        "source_bars": bars,
        "technical": {
            "status": validated.status,
            "reason": validated.reason,
            "vwap": validated.vwap,
            "opening_range": validated.opening_range,
            "gaps": validated.gaps,
            "volume_basis": validated.volume_basis,
        },
        "strategy_version": strategy_version,
        "calculated_at": stamp,
        "observed_at": (validated.bars[-1]["t"].isoformat()
                        if validated.bars else None),
        "research_link": candidate.get("research_link"),
        "rejection_reasons": [candidate.get("rejection_reason", "")] if
        candidate.get("rejection_reason") else [],
    }
    record["evidence_sha256"] = _digest({k: v for k, v in record.items()
                                         if k != "evidence_sha256"})
    date = str(candidate.get("signal_at", ""))[:10] or stamp[:10]
    folder = Path(root) / "outputs/signals/archive" / date
    folder.mkdir(parents=True, exist_ok=True)
    previous = sorted(folder.glob("*.json"))
    prev_hash = ""
    if previous:
        try:
            prev_doc = json.loads(previous[-1].read_text(encoding="utf-8"))
            prev_hash = str(prev_doc.get("chain_sha256", ""))
        except (OSError, ValueError, UnicodeError):
            prev_hash = ""
    record["prev_chain_sha256"] = prev_hash
    record["chain_sha256"] = _digest({k: v for k, v in record.items()
                                      if k not in {"chain_sha256", "evidence_sha256"}} |
                                     {"evidence_sha256": record["evidence_sha256"],
                                      "prev_chain_sha256": prev_hash})
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_"
                      for c in str(candidate.get("signal_id", "signal")))[:80]
    name = f"{safe_id}-{record['evidence_sha256'][:12]}.json"
    path = folder / name
    if path.exists():
        return path  # idempotent: identical evidence already archived
    path.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def verify_archive(path: Path) -> tuple[bool, str]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return False, f"unreadable: {type(exc).__name__}"
    expected = doc.get("evidence_sha256")
    actual = _digest({k: v for k, v in doc.items()
                      if k not in {"evidence_sha256", "chain_sha256",
                                   "prev_chain_sha256"}})
    if expected != actual:
        return False, "evidence hash mismatch"
    chain_expected = doc.get("chain_sha256")
    if chain_expected:
        chain_actual = _digest({
            k: v for k, v in doc.items() if k != "chain_sha256"})
        if chain_expected != chain_actual:
            return False, "chain hash mismatch"
    return True, "ok"


def replay(root: Path, date: str) -> list[dict]:
    """Deterministic replay of archived observations, newest first."""
    folder = Path(root) / "outputs/signals/archive" / date
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("*.json"), key=lambda p: p.name):
        ok, reason = verify_archive(path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows.append({"path": str(path), "integrity": ok, "reason": reason,
                     "signal": doc.get("signal"), "technical": doc.get("technical"),
                     "observed_at": doc.get("observed_at")})
    return rows


def _parse(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
