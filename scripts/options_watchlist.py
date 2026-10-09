#!/usr/bin/env python3
"""3:10pm weekday options watchlist -> Slack. Research only, never orders.

For each watchlist ticker: live spot, best long call + best long put
(delta/liquidity filtered), formatted for quick reading. Default tickers
come from the latest screener top-5.

  ../TradingAgents/.venv/bin/python scripts/options_watchlist.py --dry-run
  ../TradingAgents/.venv/bin/python scripts/options_watchlist.py --send
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="End-of-day options watchlist.")
    p.add_argument("--tickers", default="")
    p.add_argument("--screen", default="outputs/screener/latest.json")
    p.add_argument("--screen-top", type=int, default=5)
    p.add_argument("--premium-cap", type=Decimal, default=Decimal("1200"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--send", action="store_true")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def format_watchlist(rows: list[dict], today: str) -> str:
    """Pure formatter (tested). Compact, scannable, mobile-friendly."""
    lines = [f"*Options watch — {today} close*",
             "_Calls above, puts below. Premium per 1 contract. Desk decides at 15:45._"]
    for r in rows:
        if "spot" not in r:
            lines.append(f"\n*{r['ticker']}* — skipped ({r.get('error', 'no data')})")
            continue
        lines.append(
            f"\n*{r['ticker']}* @ ${r['spot']} ({r['spot_source']})")
        for side in ("call", "put"):
            leg = r.get(side)
            if not leg:
                lines.append(f"  • {side}: _no liquid candidate_")
                continue
            flag = " ⚠️ over cap" if leg.get("over_cap") else ""
            lines.append(
                f"  • {side}: *{leg['contract']}* K{leg['strike']} "
                f"bid {leg['bid']}/ask {leg['ask']} Δ{leg['delta']} "
                f"IV{leg['iv']} `${leg['premium']}`{flag}")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = parse_args(argv)
    from robinhood_tools.alpaca_market_data import AlpacaMarketDataHttpClient
    from robinhood_tools.alpaca_options import AlpacaOptionsData
    from robinhood_tools.market_prices import get_quote
    from robinhood_tools.options_flow import pick_long_option
    from robinhood_tools.settings import load_env
    import os

    env = {**load_env(args.env_file), **os.environ}
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        try:
            screen = json.loads(Path(args.screen).read_text())
            tickers = [r["symbol"].upper().replace(".", "-")
                       for r in screen.get("ranked", [])[:args.screen_top]]
        except Exception as exc:
            print(f"no tickers (screen unreadable: {type(exc).__name__})")
            return 2
    if not tickers:
        print("empty watchlist.")
        return 2

    data = AlpacaOptionsData.from_values(env)
    md = AlpacaMarketDataHttpClient.from_values(env)
    today = date.today()
    rows = []
    for ticker in tickers:
        row = {"ticker": ticker}
        try:
            spot_q = get_quote(ticker, data_client=md, max_age_minutes=60)
            row.update({"spot": str(round(spot_q.price, 2)), "spot_source": spot_q.source})
            for direction, side in (("bullish", "call"), ("bearish", "put")):
                try:
                    quote, _ = pick_long_option(
                        data=data, underlying=ticker, spot=spot_q.price,
                        direction=direction, today=today)
                    premium = (quote.ask * 100).quantize(Decimal("0.01"))
                    row[side] = {"contract": quote.contract.option_symbol,
                                 "strike": str(quote.contract.strike),
                                 "bid": str(quote.bid), "ask": str(quote.ask),
                                 "delta": str(quote.delta), "iv": str(quote.iv),
                                 "premium": str(premium),
                                 "over_cap": premium > args.premium_cap}
                except Exception as exc:
                    row[side] = None
                    row[f"{side}_error"] = f"{type(exc).__name__}"
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        rows.append(row)
        print(json.dumps(row, default=str))

    message = format_watchlist(rows, today.isoformat())
    if args.send:
        sys.path.insert(0, ".")
        from scripts.notify import send

        result = send(message)
        print("sent", result.get("message_ts", ""))
    elif not args.dry_run:
        print("--- preview (use --send to post) ---")
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
