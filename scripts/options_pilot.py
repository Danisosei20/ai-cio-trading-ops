#!/usr/bin/env python3
"""Paper long-options pilot: scout chain -> select -> gated placement.

Owner-authorized 2026-10-07: long calls/puts, paper only. 0DTE, naked,
and short premium stay disabled; every order passes review + ledger +
session guard. Sells-to-close use --close with a held OCC symbol.

  ../TradingAgents/.venv/bin/python scripts/options_pilot.py --ticker NVDA --direction call --dry-run
  ../TradingAgents/.venv/bin/python scripts/options_pilot.py --ticker NVDA --direction call --execute
  ../TradingAgents/.venv/bin/python scripts/options_pilot.py --close NVDA261120C00240000 --execute
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.options_flow import pick_long_option, place_long_option  # noqa: E402
from robinhood_tools.paper_flow import lookup_earnings_date  # noqa: E402
from robinhood_tools.runtime import build_settings  # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Paper long-options pilot.")
    p.add_argument("--ticker", default="NVDA")
    p.add_argument("--direction", default="call", choices=["call", "put"])
    p.add_argument("--premium-cap", type=Decimal, default=Decimal("250"))
    p.add_argument("--max-age-minutes", type=int, default=5,
                   help="quote freshness; raise explicitly for after-hours scouting")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--close", default="", help="OCC symbol to sell-to-close")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    settings = build_settings(args.config, args.env_file)
    if settings.trading_enabled:
        print("REFUSAL: live kill switch open.", file=sys.stderr)
        return 2

    import os
    from datetime import datetime, timezone

    from robinhood_tools.alpaca_options import AlpacaOptionsData
    from robinhood_tools.market_prices import get_quote
    from robinhood_tools.alpaca_market_data import AlpacaMarketDataHttpClient
    from robinhood_tools.settings import load_env
    from robinhood_tools.universe import Sp500Snapshot
    import urllib.request
    import re

    env = {**load_env(args.env_file), **os.environ}
    ticker = args.ticker.strip().upper()
    today = date.today()

    if args.close:
        print(json.dumps({"mode": "close", "action": "manual-close",
                          "note": "use the desk guard/exit path; closes travel review+ledger"}))
        return 0

    data = AlpacaOptionsData.from_values(env)
    spot_q = get_quote(ticker, data_client=AlpacaMarketDataHttpClient.from_values(env),
                       max_age_minutes=args.max_age_minutes)
    spot = spot_q.price
    print(f"{ticker} spot {spot} ({spot_q.source} {spot_q.as_of})")

    req = urllib.request.Request("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                                 headers={"User-Agent": "desk-options/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        page = resp.read().decode("utf-8", "replace")
    m = re.search(r"List of S&amp;P 500 companies.*?(<table.*?</table>)", page, re.S)
    table = m.group(1) if m else page
    members = {s.upper().replace(".", "-") for s in re.findall(r">([A-Z]{1,5}(?:\.[A-Z])?)<", table)}
    snapshot = Sp500Snapshot(symbols=frozenset(members),
                             as_of=datetime.now(timezone.utc).isoformat(),
                             source_url="https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")

    quote, rejected = pick_long_option(
        data=data, underlying=ticker, spot=spot,
        direction="bullish" if args.direction == "call" else "bearish", today=today)
    contracts = int(args.premium_cap // (quote.ask * 100))
    if contracts < 1:
        print(json.dumps({"ticker": ticker, "action": "skip",
                          "reason": f"ask {quote.ask} exceeds ${args.premium_cap} cap"}))
        return 0
    rec = {"ticker": ticker, "contract": quote.contract.option_symbol,
           "expiry": quote.contract.expiry, "strike": str(quote.contract.strike),
           "bid": str(quote.bid), "ask": str(quote.ask), "delta": str(quote.delta),
           "iv": str(quote.iv), "qty": contracts,
           "premium": str((Decimal(contracts) * quote.ask * 100).quantize(Decimal("0.01"))),
           "rejected_elsewhere": len(rejected)}
    if args.dry_run or not args.execute:
        rec["action"] = "dry_run_buy"
        print(json.dumps(rec, indent=2))
        return 0
    result = place_long_option(
        settings=settings, snapshot=snapshot, quote=quote, contracts=contracts,
        earnings_date=lookup_earnings_date(ticker), premium_cap=args.premium_cap)
    rec.update({"action": "buy_placed", "order_id": result["order_id"],
                "status": result["status"], "approval_id": result["approval_id"]})
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
