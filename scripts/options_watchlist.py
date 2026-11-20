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
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="End-of-day options watchlist.")
    p.add_argument("--tickers", default="")
    p.add_argument("--screen", default="outputs/screener/latest.json")
    p.add_argument("--screen-top", type=int, default=5)
    p.add_argument("--premium-cap", type=Decimal, default=Decimal("500"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--send", action="store_true")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def format_watchlist(rows: list[dict], today: str,
                     generated_at: str | None = None) -> str:
    """Pure formatter (tested). Compact, scannable, mobile-friendly.

    The header used to read "{today} close" and "Desk decides at 15:45",
    which was written for the single 15:10 run. The all-day loop sends this
    on every placement, so it now says when it was produced and that the
    decision has already been acted on - a stale header made a mid-session
    placement look like a pending end-of-day watch.
    """
    stamp = f" at {generated_at}" if generated_at else ""
    lines = [f"*Options watch — {today}{stamp}*",
             "_Calls above, puts below. Premium per 1 contract. "
             "Entries are placed by the gated flow as watchlists fire; "
             "exits are managed by the options guard._"]
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


def periodic_check_status(now, *, clock, calendar):
    """Central session gate used before any periodic scan or Slack send."""
    from robinhood_tools.market_calendar import entry_session_status

    return entry_session_status(now, clock=clock, calendar=calendar)


def main(argv=None, *, now=None, clock=None, calendar=None) -> int:
    args = parse_args(argv)
    from robinhood_tools.alpaca_market_data import AlpacaMarketDataHttpClient
    from robinhood_tools.alpaca_options import AlpacaOptionsData
    from robinhood_tools.alpaca_paper import AlpacaPaperHttpTransport
    from robinhood_tools.market_calendar import AlpacaExchangeCalendar
    from robinhood_tools.market_prices import get_quote
    from robinhood_tools.options_flow import pick_long_option
    from robinhood_tools.settings import load_env
    import os

    env = {**load_env(args.env_file), **os.environ}
    if now is None:
        now = datetime.now(ZoneInfo("America/New_York"))
    if clock is None or calendar is None:
        try:
            transport = AlpacaPaperHttpTransport.from_values(env)
            clock = transport.request("GET", "/v2/clock")
            calendar = AlpacaExchangeCalendar(transport)
        except Exception:
            # A periodic job must fail closed and quietly when its session
            # dependencies cannot be established.
            clock = None
            calendar = None
    session = periodic_check_status(now, clock=clock, calendar=calendar)
    if not session["can_periodic_alert"]:
        print(json.dumps({"periodic_check_skipped": session}, sort_keys=True))
        return 0
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
    today = now.astimezone(ZoneInfo("America/New_York")).date()
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

    generated = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M ET")
    message = format_watchlist(rows, today.isoformat(), generated)
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
