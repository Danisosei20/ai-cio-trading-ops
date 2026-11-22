"""Assemble a read-only, fail-closed market payload from local desk artifacts.

Bars come from the BarReader artifact (``outputs/orchestrator/*/bars_<SYM>.json``),
zone levels from the zone profile (``outputs/zones/*/<SYM>.json``), and trade
direction from the latest paper-auto decision (``auto.json``). Every emitted
level carries the source artifact (and its timestamp) or is explicitly marked
as computed. A missing, empty or unparsable required artifact yields an
``unavailable`` payload rather than a reassuring guessed market picture.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

PUT_ACTIONS = ("sell", "underweight", "put")


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
    """Coerce a scalar to a finite float; reject bools, text and NaN/inf."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rel(root: Path, path: Path) -> str:
    """Stable relative artifact path for source annotations."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _artifact_source(root: Path, path: Path, doc: dict) -> str:
    stamp = str(doc.get("generated_at") or "")
    return f"{_rel(root, path)}@{stamp}" if stamp else _rel(root, path)


def _nodes(raw: object) -> list[float]:
    """Zone nodes are either floats or ``{"price": float}`` records."""
    result: list[float] = []
    for node in raw if isinstance(raw, list) else []:
        value = node.get("price") if isinstance(node, dict) else node
        number = _finite(value)
        if number is not None:
            result.append(number)
    return result


def _latest(root: Path, pattern: str) -> Path | None:
    try:
        matches = sorted(root.glob(pattern))
    except OSError:
        return None
    return matches[-1] if matches else None


def market_payload(root: Path, symbol: str, *, now_iso: str) -> dict:
    """Assemble a sourced market snapshot. Never raises on bad artifacts.

    Returns ``{"status": "unavailable", "reason": ...}`` when either required
    artifact is missing/empty/unparsable, otherwise a payload with bars, zone
    levels, direction and computed or auto-sourced stop/target levels.
    """
    root = Path(root)

    bars_path = _latest(root, f"outputs/orchestrator/*/bars_{symbol}.json")
    if bars_path is None:
        return _unavailable(f"no bars artifact for {symbol}")
    bars_doc = _read_json(bars_path)
    if bars_doc is None:
        return _unavailable(f"unparsable bars artifact for {symbol}")
    bars = bars_doc.get("bars")
    if not isinstance(bars, list) or not bars:
        return _unavailable(f"empty bars artifact for {symbol}")
    last_bar = bars[-1]
    if not isinstance(last_bar, dict):
        return _unavailable(f"malformed last bar for {symbol}")
    close = _finite(last_bar.get("close"))
    if close is None:
        return _unavailable(f"non-finite last close for {symbol}")

    zone_path = _latest(root, f"outputs/zones/*/{symbol}.json")
    if zone_path is None:
        return _unavailable(f"no zone artifact for {symbol}")
    zone_doc = _read_json(zone_path)
    if zone_doc is None:
        return _unavailable(f"unparsable zone artifact for {symbol}")
    profile = zone_doc.get("profile")
    if not isinstance(profile, dict) or not profile:
        return _unavailable(f"empty zone profile for {symbol}")

    auto_path = _latest(root, f"outputs/paper/tradingagents/{symbol}/*/auto.json")
    auto_doc = _read_json(auto_path) if auto_path is not None else None
    if auto_doc is None:
        direction = "call"
        direction_source = "computed-unknown"
        auto_source = ""
    else:
        action = str(auto_doc.get("action") or "").strip().lower()
        direction = "put" if action in PUT_ACTIONS else "call"
        auto_source = _artifact_source(root, auto_path, auto_doc)
        direction_source = auto_source

    zone_source = _artifact_source(root, zone_path, zone_doc)
    levels: dict[str, dict] = {
        "poc": {"value": _finite(profile.get("poc")), "source": zone_source},
        "vah": {"value": _finite(profile.get("vah")), "source": zone_source},
        "val": {"value": _finite(profile.get("val")), "source": zone_source},
        "support": {"value": _finite(profile.get("low")), "source": zone_source},
        "resistance": {"value": _finite(profile.get("high")), "source": zone_source},
        "supply": {"value": _nodes(profile.get("supply_nodes")), "source": zone_source},
        "demand": {"value": _nodes(profile.get("demand_nodes")), "source": zone_source},
    }

    # Risk is 3% of the last close in the computed case. A put's stop sits
    # above the close, so its risk (and therefore its targets) run downward.
    step = close * 0.03
    stop = close - step if direction == "call" else close + step
    tp1 = close + 2 * step if direction == "call" else close - 2 * step
    tp2 = close + 3 * step if direction == "call" else close - 3 * step
    computed_source = "computed-2R-3R"
    stop_source = tp1_source = tp2_source = computed_source
    if auto_doc is not None:
        auto_stop = _finite(auto_doc.get("stop_price"))
        if auto_stop is not None:
            stop = auto_stop
            stop_source = f"{auto_source}#stop_price"
            risk = abs(close - stop)
            step = risk if risk > 0 else step
        auto_target = _finite(auto_doc.get("target_price"))
        if auto_target is not None:
            tp1 = auto_target
            tp1_source = f"{auto_source}#target_price"
            tp2 = tp1 + step if direction == "call" else tp1 - step

    levels["stop"] = {"value": stop, "source": stop_source}
    levels["tp1"] = {"value": tp1, "source": tp1_source}
    levels["tp2"] = {"value": tp2, "source": tp2_source}

    sources = [
        f"bars:{_artifact_source(root, bars_path, bars_doc)}",
        f"zones:{zone_source}",
        f"direction:{direction_source}",
    ]
    return {
        "status": "ok",
        "symbol": symbol,
        "generated_at": now_iso,
        "bars": bars,
        "levels": levels,
        "direction": direction,
        "sources": sources,
    }
