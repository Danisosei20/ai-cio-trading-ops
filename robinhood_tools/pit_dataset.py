#!/usr/bin/env python3
"""Immutable multi-session point-in-time dataset indexing.

Reuses market_snapshot for HMAC-bound, path-identity-bound snapshot writes.
Adds explicit data-type labeling, duplicate detection, decision-time leakage
guards, calendar-driven missing-session checks and tamper verification.
No competing evidence store is created.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from robinhood_tools import market_snapshot

DATA_TYPES = ("signal_time_snapshot", "captured_live_session",
              "historical_backfill", "synthetic_fixture")
SPLITS = ("dev", "validation", "final")
SCHEMA = "pit-dataset-v1"
SPEC_VERSION = "intraday-setups/v1.0.0"


class DatasetError(ValueError):
    pass


def bars_digest(bars: list[dict]) -> str:
    return hashlib.sha256(json.dumps(
        bars, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str).encode("utf-8")).hexdigest()


def dataset_root(root: Path, dataset_id: str) -> Path:
    if not dataset_id or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
                             for c in dataset_id):
        raise DatasetError("invalid dataset_id")
    return Path(root) / "outputs" / "pit_datasets" / dataset_id


def _bar_time(bar: dict) -> datetime | None:
    raw = bar.get("t") or bar.get("timestamp") or bar.get("datetime")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _leak_reason(bars: list[dict], decision_time: str) -> str:
    try:
        decision = datetime.fromisoformat(decision_time)
    except ValueError:
        return "invalid decision_time"
    stamps = [_bar_time(b) for b in bars]
    if any(s is None for s in stamps):
        return "bar without parseable timestamp"
    if stamps != sorted(stamps):
        return "bars not ordered"
    if len({str(s) for s in stamps}) != len(stamps):
        return "duplicate bars"
    if max(stamps).astimezone() > decision.astimezone():
        return "future-bar leakage"
    return ""


def write_snapshot(root: Path, *, dataset_id: str, session_date: str,
                   symbol: str, decision_time_utc: str, data_type: str,
                   bars: list[dict], provider: str, feed: str,
                   volume_basis: str, timeframe: str, strategy_version: str,
                   split: str, retrieved_at_utc: str | None = None,
                   cycle_id: str | None = None) -> dict:
    if data_type not in DATA_TYPES:
        raise DatasetError(f"invalid data_type: {data_type!r}")
    if split not in SPLITS:
        raise DatasetError(f"invalid split: {split!r}")
    reason = _leak_reason(bars, decision_time_utc)
    if reason:
        raise DatasetError(f"snapshot rejected: {reason}")
    stamp = retrieved_at_utc or datetime.now(timezone.utc).isoformat()
    cycle = cycle_id or dataset_id
    record = market_snapshot.write_snapshot(
        root, date=session_date, cycle_id=cycle, symbol=symbol, bars=bars,
        provider=provider, feed=feed, timeframe=timeframe,
        signal_id=uuid.uuid4().hex[:16],
        freshness=data_type, completeness="complete",
        now=stamp)
    meta = {
        "schema": SCHEMA, "dataset_id": dataset_id, "session_date": session_date,
        "symbol": symbol.upper(), "decision_time_utc": decision_time_utc,
        "bar_as_of_utc": max(_bar_time(b) for b in bars).isoformat(),
        "retrieved_at_utc": stamp, "data_type": data_type, "provider": provider,
        "feed": feed, "volume_basis": volume_basis, "timeframe": timeframe,
        "strategy_version": strategy_version, "spec_version": SPEC_VERSION,
        "split": split, "bar_count": len(bars),
        "bars_digest": bars_digest(bars),
        "snapshot_path": record["path"], "evidence_sha256": record["evidence_sha256"],
        "hash_mode": "hmac-sha256",
        "key_id": hashlib.sha256(market_snapshot.snapshot_key()).hexdigest()[:16],
    }
    meta["meta_sha256"] = market_snapshot.canonical_hash(
        {k: v for k, v in meta.items() if k != "meta_sha256"})
    path = dataset_root(root, dataset_id) / f"{session_date}-{symbol.upper()}.json"
    if path.exists():
        raise DatasetError("duplicate decision key: " + str(path.name))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return meta


def verify_meta(meta: dict) -> bool:
    stored = meta.get("meta_sha256")
    actual = market_snapshot.canonical_hash(
        {k: v for k, v in meta.items() if k != "meta_sha256"})
    return bool(stored) and stored == actual


def derived_split(date: str, all_dates: list[str]) -> str:
    dates = sorted(set(all_dates))
    if len(dates) < 5:
        return "dev"
    n_dev = max(3, int(len(dates) * 0.6))
    n_val = max(1, int(len(dates) * 0.2))
    if date in dates[:n_dev]:
        return "dev"
    if date in dates[n_dev:n_dev + n_val]:
        return "validation"
    return "final"


def load_entries(root: Path, dataset_id: str) -> list[dict]:
    base = dataset_root(root, dataset_id)
    if not base.exists():
        return []
    out = []
    for path in sorted(base.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if doc.get("schema") == SCHEMA:
            out.append(doc)
    return out


def audit(root: Path, dataset_id: str, *, calendar_dates: list[str] | None = None) -> dict:
    entries = load_entries(root, dataset_id)
    problems = []
    all_dates = [e["session_date"] for e in entries]
    seen: dict[tuple[str, str, str], str] = {}
    digests: dict[str, list[str]] = {}
    for entry in entries:
        key = (entry["session_date"], entry["symbol"], entry["decision_time_utc"])
        if key in seen:
            problems.append(f"duplicate decision key: {key}")
        seen[key] = entry["snapshot_path"]
        if not verify_meta(entry):
            problems.append("dataset meta HMAC mismatch")
        if derived_split(entry["session_date"], all_dates) != entry["split"]:
            problems.append("split/source mismatch")
        ok, reason = market_snapshot.verify_snapshot(Path(entry["snapshot_path"]))
        if ok:
            inner = json.loads(Path(entry["snapshot_path"]).read_text(encoding="utf-8"))
            if bars_digest(inner.get("bars", [])) != entry.get("bars_digest"):
                problems.append("bars digest mismatch with verified snapshot")
        digests.setdefault(entry["bars_digest"], []).append(key[0] + "/" + key[1])
        if entry["data_type"] in {"historical_backfill", "synthetic_fixture"}:
            problems.append(f"evaluation-only data type: {entry['data_type']}")
        ok, reason = market_snapshot.verify_snapshot(Path(entry["snapshot_path"]))
        if not ok:
            problems.append(f"snapshot integrity: {reason}")
        try:
            retrieved = datetime.fromisoformat(entry["retrieved_at_utc"])
            decided = datetime.fromisoformat(entry["decision_time_utc"])
            if retrieved.astimezone() < decided.astimezone():
                problems.append("retrieval before decision time")
        except ValueError:
            problems.append("unparseable retrieval/decision timestamp")
    for digest, keys in digests.items():
        if len(keys) > 1:
            problems.append(f"duplicate bar set across sessions: {','.join(keys)}")
    missing = []
    if calendar_dates:
        present = {e["session_date"] for e in entries}
        missing = [d for d in calendar_dates if d not in present]
    accepted = not problems and not missing
    return {
        "track": "pit_dataset_audit", "dataset_id": dataset_id,
        "entries": len(entries), "sessions": len({e["session_date"] for e in entries}),
        "splits": {s: sum(1 for e in entries if e["split"] == s) for s in SPLITS},
        "data_types": sorted({e["data_type"] for e in entries}),
        "problems": problems, "missing_sessions": missing,
        "accepted_for_replay": accepted,
        "verdict": "ACCEPTED_FOR_REPLAY" if accepted else "NOT_VERIFIED",
        "note": "historical_backfill and synthetic_fixture are evaluation-only.",
    }
