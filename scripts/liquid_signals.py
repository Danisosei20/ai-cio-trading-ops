#!/usr/bin/env python3
"""Refresh Liquid signal cache for the desk fleet.

Read-only signal gathering from Co-Invest MCP (news, unusual activity,
positioning pulse, leaderboard). Writes outputs/liquid/latest.json which
the fleet's research task and options watch-trade entry gate consume.

Never places trades — signal intelligence only.

Usage (run by the agent with MCP access):
  python3 scripts/liquid_signals.py --refresh

Then the fleet picks it up on the next --plan research run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

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


def crowded_longs() -> list[str]:
    """Symbols that are crowded longs where smart money is fading."""
    data = load_signals()
    return data.get("crowded_longs_smart_money_fading", [])


def crowded_shorts() -> list[str]:
    """Symbols that are crowded shorts where smart money is buying."""
    data = load_signals()
    return data.get("crowded_shorts_smart_money_buying", [])


def signal_note() -> str:
    """Human-readable summary of current Liquid signals."""
    data = load_signals()
    if not data:
        return "liquid: no signals file"
    cl = len(data.get("crowded_longs_smart_money_fading", []))
    cs = len(data.get("crowded_shorts_smart_money_buying", []))
    return f"liquid: {cl} crowded longs fading / {cs} crowded shorts fading"


def refresh_from_mcp(news: dict, positioning: dict, leaderboard: list[dict],
                     account: str) -> dict:
    """Build the signals JSON from MCP tool output.

    Called by the agent after fetching news, positioning, and leaderboard
    from the Co-Invest MCP. Writes outputs/liquid/latest.json.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

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
    SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIGNALS_PATH.write_text(json.dumps(data, indent=2))
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Liquid signal cache refresh.")
    parser.add_argument("--refresh", action="store_true",
                        help="Refresh signals from MCP (run by agent with MCP access)")
    args = parser.parse_args(argv)

    if args.refresh:
        print("Use the Co-Invest MCP tools (get_news, get_positioning_pulse, "
              "leaderboard_data) and pass results to refresh_from_mcp().")
        return 0

    print(signal_note())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
