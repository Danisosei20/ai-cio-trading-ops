#!/usr/bin/env python3
"""Daily read-only AI news readout for the desk's biggest movers.

Selects the symbols with the largest absolute move from the freshest local
bar artifacts, gathers source-cited news via news_scout.py, asks news_explainer
for an explanation per symbol, and writes one dated artifact. Research only:
no gate, no order path, no write endpoint in the viewer.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
BARS_GLOB = "outputs/orchestrator/*/bars_*.json"
PREFIX = "bars_"


def _freshest_bars(root: Path) -> list[Path]:
    by_symbol: dict[str, Path] = {}
    for path in root.glob(BARS_GLOB):
        if not path.is_file():
            continue
        symbol = path.stem[len(PREFIX):]
        current = by_symbol.get(symbol)
        if current is None or (path.stat().st_mtime, str(path)) > (
                current.stat().st_mtime, str(current)):
            by_symbol[symbol] = path
    return sorted(by_symbol.values(), key=lambda p: p.name)


def _move(path: Path) -> tuple[str, float] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    bars = doc.get("bars")
    if not isinstance(bars, list) or not bars:
        return None
    sessions: dict[str, list[float]] = {}
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        ts = str(bar.get("ts") or "")
        close = bar.get("close")
        if len(ts) < 10:
            continue
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if value > 0:
            sessions.setdefault(ts[:10], []).append(value)
    if len(sessions) < 2:
        return None
    keys = sorted(sessions)
    first, last = sessions[keys[0]][0], sessions[keys[-1]][-1]
    return path.stem[len(PREFIX):], round((last / first - 1) * 100, 2)


def select_movers(root: Path, *, top: int = 8) -> list[tuple[str, float]]:
    """Freshest bar per symbol, ranked by absolute move, strongest first."""
    if not isinstance(top, int) or isinstance(top, bool) or top <= 0:
        return []
    scored = [m for p in _freshest_bars(root) if (m := _move(p)) is not None]
    scored.sort(key=lambda pair: (-abs(pair[1]), pair[0]))
    return scored[:top]


def _default_scout(root: Path, symbols: list[str]) -> dict:
    out = root / "outputs/news/latest.json"
    proc = subprocess.run(
        [sys.executable, "scripts/news_scout.py", "--tickers", ",".join(symbols),
         "--out", str(out)], cwd=root, capture_output=True, text=True, timeout=1800)
    if not out.exists():
        return {"generated_at": datetime.now(ET).isoformat(), "tickers": [],
                "error": f"news_scout rc={proc.returncode}: {proc.stderr[-200:]}"}
    return json.loads(out.read_text(encoding="utf-8"))


def _default_explain(root: Path, symbols: list[str], docs: dict) -> dict:
    from scripts.news_explainer import explain_moves
    return explain_moves(root, symbols, news_docs=docs)


def news_readout(root: Path, *, top: int = 8,
                 scout=None, explain=None) -> dict:
    """Produce and persist one dated news readout artifact."""
    root = Path(root)
    movers = select_movers(root, top=top)
    if not movers:
        return {"status": "unavailable", "reason": "no bar artifacts with two sessions"}
    symbols = [symbol for symbol, _ in movers]
    scout = scout or (lambda syms: _default_scout(root, syms))
    explain = explain or (lambda syms, docs: _default_explain(root, syms, docs))
    news = scout(symbols) or {}
    docs = {str(t.get("ticker")): t for t in (news.get("tickers") or [])
            if isinstance(t, dict) and t.get("ticker")}
    doc = explain(symbols, docs) or {}
    doc["generated_at"] = datetime.now(ET).isoformat()
    doc["selection"] = [{"symbol": s, "move_pct": m} for s, m in movers]
    doc["top"] = top
    target = root / "outputs/news/explanations"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{datetime.now(ET).date().isoformat()}.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Daily AI news readout (read-only).")
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--top", type=int, default=8)
    args = p.parse_args(argv)
    doc = news_readout(args.root, top=args.top)
    print(json.dumps({"status": doc.get("status"),
                      "symbols": len(doc.get("selection", [])),
                      "rows": len(doc.get("rows", []))}, indent=2))
    return 0 if doc.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
