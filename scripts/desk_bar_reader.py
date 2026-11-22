#!/usr/bin/env python3
"""Read one symbol's intraday bars for the paper-desk orchestrator.

This stage exists so the BarReader stage consumes a real artifact instead of a
path no script wrote. It uses the same read-only yfinance call as
``scripts/zone_analysis.py``:

    yf.Ticker(ticker).history(period=f"{sessions}d", interval="5m")

and writes a small JSON artifact to ``--out``. Any fetch failure is recorded in
the artifact itself (with ``bars: []`` and an ``error``) and exits nonzero, so
the fail-closed orchestrator cannot mistake a failed read for healthy evidence.
No network mocks or fallbacks live in this production path.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def read_bars(symbol: str, sessions: int, interval: str) -> list[dict]:
    """Fetch bars via yfinance; raise on any failure (the caller records it)."""
    import yfinance as yf

    bars = yf.Ticker(symbol).history(period=f"{sessions}d", interval=interval)
    if bars is None or len(bars) == 0:
        raise ValueError("no intraday bars")
    result: list[dict] = []
    for ts, row in bars.iterrows():
        stamp = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        result.append({
            "ts": stamp,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        })
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True, help="path to write the bars JSON artifact")
    p.add_argument("--symbol", default="NVDA", help="ticker symbol (default NVDA)")
    p.add_argument("--sessions", type=int, default=5, help="lookback sessions (default 5)")
    p.add_argument("--interval", default="5m", help="bar interval (default 5m)")
    args = p.parse_args(argv)

    doc: dict = {
        "symbol": args.symbol,
        "bars": [],
        "source": "yfinance",
        "generated_at": datetime.now(ET).isoformat(),
    }
    try:
        doc["bars"] = read_bars(args.symbol, args.sessions, args.interval)
    except Exception as exc:  # noqa: BLE001 - any fetch error must fail closed
        doc["error"] = f"{type(exc).__name__}: {exc}"

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return 1 if "error" in doc else 0


if __name__ == "__main__":
    sys.exit(main())
