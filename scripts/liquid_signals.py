#!/usr/bin/env python3
"""Refresh Liquid signal cache for the desk fleet.

Read-only signal gathering (news, unusual activity, positioning pulse,
leaderboard). Writes outputs/liquid/latest.json which the fleet's research
task and options watch-trade entry gate consume.

Never places trades — signal intelligence only.

Usage:
  # Inspect the current cache
  python3 scripts/liquid_signals.py

  # Refresh from a JSON payload supplied by an agent with a signal feed
  python3 scripts/liquid_signals.py --from-json payload.json --account paper

The payload must contain ``positioning`` with ``long_heavy_crowd`` and/or
``short_heavy_crowd`` entries. Empty or malformed positioning fails closed:
no cache is written.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_PATH = ROOT / "outputs/liquid" / "latest.json"


def load_signals() -> dict:
    """Load the current Liquid signals cache."""
    if not SIGNALS_PATH.exists():
        return {}
    try:
        return json.loads(SIGNALS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _signals(data: dict) -> dict:
    """Read the nested signals block (older caches may be flat)."""
    nested = data.get("signals")
    return nested if isinstance(nested, dict) else data


def crowded_longs() -> list[str]:
    """Symbols that are crowded longs where smart money is fading."""
    return _signals(load_signals()).get("crowded_longs_smart_money_fading", [])


def crowded_shorts() -> list[str]:
    """Symbols that are crowded shorts where smart money is buying."""
    return _signals(load_signals()).get("crowded_shorts_smart_money_buying", [])


def signal_note() -> str:
    """Human-readable summary of current Liquid signals."""
    data = load_signals()
    if not data:
        return "liquid: no signals file"
    signals = _signals(data)
    cl = len(signals.get("crowded_longs_smart_money_fading", []))
    cs = len(signals.get("crowded_shorts_smart_money_buying", []))
    return f"liquid: {cl} crowded longs fading / {cs} crowded shorts fading"


def _write_cache(data: dict) -> dict:
    SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIGNALS_PATH.write_text(json.dumps(data, indent=2))
    return data


def refresh_from_mcp(news: dict, positioning: dict, leaderboard: list[dict],
                     account: str) -> dict:
    """Build the signals JSON from MCP tool output.

    Called by the agent after fetching news, positioning, and leaderboard.
    Writes outputs/liquid/latest.json.
    """
    now = datetime.now(ZoneInfo("America/New_York")).isoformat()

    cl = [s["symbol"] for s in positioning.get("long_heavy_crowd", [])]
    cs = [s["symbol"] for s in positioning.get("short_heavy_crowd", [])]

    data = {
        "updated_at": now,
        "source": "liquid_co_invest_mcp",
        "account": account,
        "news": news,
        "positioning": positioning,
        "leaderboard": leaderboard[:5],
        "signals": {
            "crowded_longs_smart_money_fading": cl,
            "crowded_shorts_smart_money_buying": cs,
            "smart_money_long_bias": cs,
            "smart_money_short_bias": cl,
        },
    }
    return _write_cache(data)


def _normal_symbol(raw) -> str:
    if not isinstance(raw, str):
        return ""
    value = raw.strip().split(":")[-1].strip().upper()
    if not value or not any(c.isalnum() for c in value):
        return ""
    if not all(c.isalnum() or c in ".-" for c in value):
        return ""
    return value


def _normal_entries(entries) -> list[dict]:
    out: list[dict] = []
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        symbol = _normal_symbol(entry.get("symbol"))
        if not symbol:
            continue
        item = dict(entry)
        item["symbol"] = symbol
        out.append(item)
    return out


def refresh_from_payload(payload: dict, *, account: str = "paper-auto",
                         source: str = "tradingview_mcp") -> dict:
    """Validate an agent-supplied signal payload and write the cache.

    Fails closed when there is no usable positioning evidence: malformed or
    empty entries raise ValueError and no cache is written.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    positioning = payload.get("positioning")
    if not isinstance(positioning, dict):
        raise ValueError("payload.positioning must be an object")
    longs = _normal_entries(positioning.get("long_heavy_crowd"))
    shorts = _normal_entries(positioning.get("short_heavy_crowd"))
    if not longs and not shorts:
        raise ValueError("no usable positioning evidence")

    news = payload.get("news")
    if not isinstance(news, dict):
        news = {"items": [], "note": "no news evidence"}
    leaderboard = _normal_entries(payload.get("leaderboard"))[:5]
    now = datetime.now(ZoneInfo("America/New_York")).isoformat()
    data = {
        "updated_at": now,
        "source": source,
        "account": account,
        "news": news,
        "positioning": {"long_heavy_crowd": longs, "short_heavy_crowd": shorts},
        "leaderboard": leaderboard,
        "signals": {
            "crowded_longs_smart_money_fading": [e["symbol"] for e in longs],
            "crowded_shorts_smart_money_buying": [e["symbol"] for e in shorts],
            "smart_money_long_bias": [e["symbol"] for e in shorts],
            "smart_money_short_bias": [e["symbol"] for e in longs],
        },
    }
    return _write_cache(data)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Liquid signal cache refresh.")
    parser.add_argument("--refresh", action="store_true",
                        help="Refresh signals from MCP (run by agent with MCP access)")
    parser.add_argument("--from-json", metavar="PATH",
                        help="path to an agent-supplied signal payload JSON file")
    parser.add_argument("--account", default="paper-auto",
                        help="account label recorded in the cache")
    parser.add_argument("--source", default="tradingview_mcp",
                        help="source label recorded in the cache")
    args = parser.parse_args(argv)

    if args.from_json:
        try:
            payload = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
            refresh_from_payload(payload, account=args.account, source=args.source)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"liquid signals refresh failed: {exc}", file=sys.stderr)
            return 2
        print(f"liquid signals refreshed: {SIGNALS_PATH}")
        return 0

    if args.refresh:
        print("Use a signal-feed tool to build a payload, then run "
              "scripts/liquid_signals.py --from-json <payload.json>.")
        return 0

    print(signal_note())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
