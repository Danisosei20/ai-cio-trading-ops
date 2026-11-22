"""Assemble a read-only, fail-closed option-candidate payload from desk artifacts.

Candidates come from the desk's own Time Machine artifact and rejections from
its gate dry-run artifact (``outputs/simulation/*options_*.json``). No manifest
and no hardcoded ticker is consulted: the artifacts on disk are the only source
of truth. Every emitted row carries the artifact (and timestamp) that produced
it, non-finite numbers drop their row, and a missing/broken required artifact
fails closed to ``unavailable`` rather than a reassuring guessed empty table.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

TIME_MACHINE_GLOB = "outputs/simulation/*options_time_machine*.json"
GATE_GLOB = "outputs/simulation/*options_gate_dryrun*.json"

# candidate field -> desk artifact key
NUMERIC_KEYS = {
    "strike": "strike",
    "premium": "premium0",
    "delta": "delta",
    "ev_pct": "ev_pct_on_premium",
    "ev_usd": "ev_usd_per_contract",
    "target_first": "target_first",
    "stop_first": "stop_first",
}
TEXT_KEYS = {
    "contract": "symbol",
    "underlying": "underlying",
    "right": "right",
    "expiry": "expiry",
}


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason}


def _read_json(path: Path) -> dict | None:
    """Read one JSON object, or None on any read/parse/shape failure."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _finite(value: object) -> float | None:
    """Coerce a scalar (including a numeric string) to a finite float."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object) -> str:
    """String fields are strings; a missing field is an empty string."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _rel(root: Path, path: Path) -> str:
    """Stable relative artifact path for source annotations."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _artifact_source(root: Path, path: Path, doc: dict) -> str:
    stamp = _text(doc.get("generated_at"))
    return f"{_rel(root, path)}@{stamp}" if stamp else _rel(root, path)


def _latest(root: Path, pattern: str) -> Path | None:
    try:
        matches = sorted(root.glob(pattern))
    except OSError:
        return None
    return matches[-1] if matches else None


def _candidates(rows: object, source: str) -> list[dict]:
    """Map Time Machine results to candidate rows; drop non-finite rows."""
    result: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        numbers: dict[str, float] = {}
        valid = True
        for field, key in NUMERIC_KEYS.items():
            number = _finite(row.get(key))
            if number is None:
                valid = False
                break
            numbers[field] = number
        dte = _finite(row.get("dte"))
        if dte is None:
            continue
        if not valid:
            continue
        candidate = {field: _text(row.get(key)) for field, key in TEXT_KEYS.items()}
        candidate.update(numbers)
        candidate["dte"] = int(dte)
        candidate["source"] = source
        result.append(candidate)
    return result


def _rejected(rows: object, source: str) -> list[dict]:
    """Map gate failures to rejection rows; skip approved and malformed rows.

    The dry-run writer emits three shapes: a quote/API failure (``error``), a
    missing vol estimate (``approved: false`` + ``reason``), and a real policy
    verdict from ``entry_odds_verdict()`` (``approved`` bool + ``reason``). All
    three are rejections when they carry an explanation; an approved verdict is
    a pass, not a rejection.
    """
    result: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if row.get("approved") is True:
            continue
        error = row.get("error")
        error_text = error.strip() if isinstance(error, str) else ""
        reason = row.get("reason")
        reason_text = reason.strip() if isinstance(reason, str) else ""
        if not error_text and not (row.get("approved") is False and reason_text):
            continue
        result.append({
            "underlying": _text(row.get("symbol")),
            "right": _text(row.get("side")),
            "reason": error_text or reason_text,
            "source": source,
        })
    return result


def option_candidates(root: Path) -> dict:
    """Assemble the desk's sourced option candidates. Never raises on bad artifacts."""
    root = Path(root)

    tm_path = _latest(root, TIME_MACHINE_GLOB)
    tm_doc = _read_json(tm_path) if tm_path is not None else None
    if tm_doc is None:
        return _unavailable("missing or unparsable options time-machine artifact")
    if not isinstance(tm_doc.get("results"), list):
        return _unavailable("malformed options time-machine results")

    gate_path = _latest(root, GATE_GLOB)
    gate_doc = _read_json(gate_path) if gate_path is not None else None
    if gate_doc is None:
        return _unavailable("missing or unparsable options gate artifact")
    if not isinstance(gate_doc.get("candidates"), list):
        return _unavailable("malformed options gate candidates")

    tm_source = _artifact_source(root, tm_path, tm_doc)
    gate_source = _artifact_source(root, gate_path, gate_doc)

    candidates = _candidates(tm_doc.get("results"), tm_source)
    rejected = _rejected(gate_doc.get("candidates"), gate_source)
    if not candidates and not rejected:
        return _unavailable("no option candidates or rejections in desk artifacts")

    generated_at = ""
    sources: list[str] = []
    if tm_doc is not None:
        generated_at = _text(tm_doc.get("generated_at"))
        sources.append(f"time_machine:{tm_source}")
    if gate_doc is not None:
        if not generated_at:
            generated_at = _text(gate_doc.get("generated_at"))
        sources.append(f"gate_dryrun:{gate_source}")
    return {
        "status": "ok",
        "generated_at": generated_at,
        "candidates": candidates,
        "rejected": rejected,
        "sources": sources,
    }
