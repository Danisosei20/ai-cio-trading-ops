"""Assemble a read-only, fail-closed short-basket signal from local bars.

The proposal ranks the desk's own locally stored bars by weakness: the weakest
(most negative) first-session-to-last-session percentage change leads the
basket. Only the freshest orchestrator run (by modification time, not filename)
is read, and only bars from sessions completed before the artifact was
generated are used — never future or partial-session bars. Every row carries
the relative artifact path and generation timestamp that produced it.

This is a signal for a human to review. It places no orders, calls no broker,
and changes no gate, risk controller, scheduler or stage agent.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

RUN_GLOB = "outputs/orchestrator/*"
BARS_NAME = "bars_*.json"
SYMBOL_PREFIX = "bars_"


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


def _date_prefix(value: object) -> str:
    """ISO date portion (YYYY-MM-DD) of a timestamp, or '' when absent."""
    text = _text(value)
    return text[:10] if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-" else ""


def _rel(root: Path, path: Path) -> str:
    """Stable relative artifact path for source annotations."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _artifact_source(root: Path, path: Path, doc: dict) -> str:
    stamp = _text(doc.get("generated_at"))
    return f"{_rel(root, path)}@{stamp}" if stamp else _rel(root, path)


def _mtime(path: Path) -> float:
    """Directory mtime, or 0.0 when the stat fails (still deterministic)."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _latest_run(root: Path) -> Path | None:
    """Freshest orchestrator run directory by mtime, lexicographic tie-break."""
    try:
        runs = [p for p in root.glob(RUN_GLOB) if p.is_dir()]
    except OSError:
        return None
    if not runs:
        return None
    # max() with an (mtime, name) key makes equal-mtime runs resolve to the
    # lexicographically greatest directory name rather than glob order.
    return max(runs, key=lambda p: (_mtime(p), p.name))


def _completed_closes(doc: dict) -> tuple[float, float] | None:
    """(first session's first close, last session's last close), or None.

    Bars are grouped by their session date and only sessions strictly before
    the artifact's generation date count as completed. A document without a
    valid generation date is unusable. Both ends must be finite and positive;
    anything else makes the symbol unusable.
    """
    bars = doc.get("bars")
    if not isinstance(bars, list) or not bars:
        return None
    cutoff = _date_prefix(doc.get("generated_at"))
    if not cutoff:
        # Fail closed: without a generation date there is no cutoff, so the
        # session filter below would be skipped and partial/future bars could
        # leak in. Such a document must be excluded, not treated as complete.
        return None
    sessions: dict[str, list[tuple[str, float]]] = {}
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        ts = _text(bar.get("ts"))
        session = _date_prefix(ts)
        if not session or (cutoff and session >= cutoff):
            continue
        close = _finite(bar.get("close"))
        if close is None or close <= 0:
            continue
        sessions.setdefault(session, []).append((ts, close))
    if len(sessions) < 2:
        return None
    ordered = sorted(sessions)
    first_bars = sorted(sessions[ordered[0]], key=lambda pair: pair[0])
    last_bars = sorted(sessions[ordered[-1]], key=lambda pair: pair[0])
    return first_bars[0][1], last_bars[-1][1]


def short_basket(root: Path, *, capital: float = 500.0, count: int = 5) -> dict:
    """Propose an equal-weight short basket from the desk's latest bar run.

    Returns ``{"status": "unavailable", "reason": ...}`` when there is no run,
    no valid symbol, or invalid sizing inputs. Otherwise returns a sourced,
    weakest-first proposal. Never raises on bad artifacts.
    """
    root = Path(root)
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return _unavailable("invalid basket count")
    money = _finite(capital)
    if money is None or money <= 0:
        return _unavailable("invalid basket capital")

    run = _latest_run(root)
    if run is None:
        return _unavailable("no orchestrator bar run artifacts")
    try:
        paths = sorted(run.glob(BARS_NAME))
    except OSError:
        return _unavailable("cannot list bars artifacts")
    if not paths:
        return _unavailable("no bars artifacts in latest orchestrator run")

    ranked: list[tuple[float, str, float, str, str]] = []
    for path in paths:
        doc = _read_json(path)
        if doc is None:
            continue
        closes = _completed_closes(doc)
        if closes is None:
            continue
        first, last = closes
        # Round away binary-float noise (e.g. 80/100 yields -19.999999999999996)
        # so the percentage is stable for ranking and display.
        change_pct = round((last / first - 1.0) * 100.0, 6)
        if not math.isfinite(change_pct):
            continue
        stamp = _text(doc.get("generated_at"))
        symbol = _text(doc.get("symbol")).strip() or path.stem[len(SYMBOL_PREFIX):]
        source = _artifact_source(root, path, doc)
        ranked.append((change_pct, symbol, last, source, stamp))

    if not ranked:
        return _unavailable("no valid symbols in latest orchestrator run")

    # Weakest (most negative) first; symbol breaks exact ties deterministically.
    ranked.sort(key=lambda row: (row[0], row[1]))
    selected = ranked[:count]
    allocation = money / count
    basket: list[dict] = []
    generated_at = ""
    for change_pct, symbol, reference_price, source, stamp in selected:
        shares = allocation / reference_price
        notional = shares * reference_price
        # A finite but subnormal reference price can overflow shares to inf.
        # Never emit a non-finite row: the proposal must be entirely numeric.
        if not math.isfinite(shares) or not math.isfinite(notional):
            continue
        basket.append({
            "symbol": symbol,
            "reference_price": reference_price,
            "change_pct": change_pct,
            "allocation": allocation,
            "shares": shares,
            "notional": notional,
            "source": source,
        })
        if not generated_at:
            generated_at = stamp

    if not basket:
        return _unavailable("no finite-sized symbols in latest orchestrator run")

    return {
        "status": "ok",
        "generated_at": generated_at,
        "capital": money,
        "count": count,
        "basket": basket,
        # Sources stay aligned with the rows actually proposed, never stale.
        "sources": [row["source"] for row in basket],
    }
