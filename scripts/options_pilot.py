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

from robinhood_tools.errors import PolicyViolation  # noqa: E402
from robinhood_tools.options_gate import DEFAULT_MIN_EV_PCT  # noqa: E402
from robinhood_tools.options_flow import pick_long_option, place_long_option  # noqa: E402
from robinhood_tools.option_entry_risk import (  # noqa: E402
    MAX_QUOTED_IV, current_vix, realized_pnl_today, validate_option_entry,
    validate_quoted_iv)
from robinhood_tools.paper_flow import lookup_earnings_date  # noqa: E402
from robinhood_tools.runtime import build_settings  # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Paper long-options pilot.")
    p.add_argument("--ticker", default="NVDA")
    p.add_argument("--direction", default="call", choices=["call", "put"])
    p.add_argument("--premium-cap", type=Decimal, default=Decimal("500"))
    p.add_argument("--max-contracts", type=int, default=1,
                   help="hard paper-desk limit: one contract")
    p.add_argument("--qty", type=int, default=0,
                   help="exact contracts (owner-directed); capped by premium-cap")
    p.add_argument("--max-positions", type=int, default=5,
                   help="max open option positions (OCC symbols held)")
    p.add_argument("--max-age-minutes", type=int, default=5,
                   help="quote freshness; raise explicitly for after-hours scouting")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--override-window", default="",
                   help="extend session as HH:MM-HH:MM for an owner-directed order "
                        "(reason = explicit CLI invocation; logged in audit)")
    p.add_argument("--min-volume", type=int, default=0,
                   help="owner-directed liquidity override: allow chains whose "
                        "volume is below the desk default (100). 0 = keep the "
                        "default. The spread, DTE, delta and OI gates are NOT "
                        "relaxed - only the volume floor.")
    p.add_argument("--close", default="", help="OCC symbol to sell-to-close")
    p.add_argument("--odds-paths", type=int, default=4000,
                   help="simulation paths for the entry odds gate")
    p.add_argument("--min-ev-pct", type=float, default=DEFAULT_MIN_EV_PCT,
                   help="minimum simulated EV on premium to open "
                         "(default +10%; the owner can "
                        "set a margin - under the winners-run policy a "
                        "capped loss against an uncapped win prices "
                         "positive by construction, so the margin is the "
                         "only part that filters)")
    p.add_argument("--max-iv", type=float, default=MAX_QUOTED_IV,
                   help="refuse quoted IV above this fraction; capped at "
                        f"{MAX_QUOTED_IV:.0%}")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def iv_cap_block(*, quoted_iv: float | None, max_iv: float) -> str | None:
    """Return an entry refusal while never exceeding the shared IV ceiling."""
    effective_cap = MAX_QUOTED_IV if max_iv <= 0 else min(max_iv, MAX_QUOTED_IV)
    try:
        validate_quoted_iv(quoted_iv, effective_cap)
    except PolicyViolation as exc:
        return str(exc).replace("option entry blocked: ", "")
    return None


def _odds_verdict(quote, ticker: str, today, args) -> dict:
    """Simulate the candidate under the guard's exit bands.

    Never raises: an odds error is itself a refusal (fail closed), the
    same posture the equity odds gate takes.
    """
    try:
        from robinhood_tools.options_gate import (
            entry_odds_verdict,
            sigma_for_underlier,
        )

        iv = float(quote.iv) if quote.iv else None
        sigma = sigma_for_underlier(ticker, iv)
        if sigma is None:
            return {"approved": False,
                    "reason": "odds gate: no vol estimate for "
                              f"{ticker} (realized and quoted IV both "
                              "missing) - fail closed"}
        return entry_odds_verdict(
            premium0=float(quote.mid()),
            delta=float(quote.delta) if quote.delta is not None else 0.5,
            spot0=float(quote.underlying_price), sigma=float(sigma),
            expiry=quote.contract.expiry, today=today,
            paths=getattr(args, "odds_paths", 4000),
            min_ev_pct=getattr(args, "min_ev_pct", DEFAULT_MIN_EV_PCT))
    except Exception as exc:  # noqa: BLE001 - no odds means no entry
        return {"approved": False,
                "reason": f"odds gate error: {type(exc).__name__}: {exc}"}


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

    daily_pnl = realized_pnl_today(settings.database_path, today)
    vix = current_vix()
    try:
        validate_option_entry(vix=vix, premium=Decimal("0"), contracts=1,
                              realized_pnl_today=daily_pnl)
    except PolicyViolation as exc:
        print(json.dumps({"ticker": ticker, "action": "skip",
                          "reason": str(exc)}))
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

    filters = None
    if args.min_volume > 0:
        from robinhood_tools.options import OptionSelectionFilters

        # Deliberate, scoped, and auditable: the spread/DTE/delta/OI gates stay
        # exactly as they are. Only the volume floor moves, and only for this
        # explicit invocation - the desk default of 100 is unchanged for every
        # other path. The cost is real: a thin chain means the exit sells into
        # whatever bid exists.
        filters = OptionSelectionFilters(min_option_volume=args.min_volume)
        print(f"[filters] owner-directed min_option_volume={args.min_volume} "
              f"(desk default 100); spread/DTE/delta/OI gates unchanged")

    quote, rejected = pick_long_option(
        data=data, underlying=ticker, spot=spot,
        direction="bullish" if args.direction == "call" else "bearish",
        today=today, filters=filters)
    iv_block = iv_cap_block(
        quoted_iv=float(quote.iv) if quote.iv is not None else None,
        max_iv=args.max_iv)
    if iv_block:
        print(json.dumps({"ticker": ticker, "action": "skip",
                          "contract": quote.contract.option_symbol,
                          "reason": iv_block}))
        return 0
    auto_qty = min(args.max_contracts, int(args.premium_cap // (quote.ask * 100)))
    contracts = args.qty if args.qty > 0 else auto_qty
    try:
        validate_option_entry(
            vix=vix, premium=Decimal(contracts) * quote.ask * 100,
            contracts=contracts, realized_pnl_today=daily_pnl)
    except PolicyViolation as exc:
        print(json.dumps({"ticker": ticker, "action": "skip",
                          "contract": quote.contract.option_symbol,
                          "reason": str(exc)}))
        return 0
    if args.qty > 0 and quote.ask * contracts * 100 > args.premium_cap:
        print(json.dumps({"ticker": ticker, "action": "skip",
                          "reason": f"directed qty {contracts} costs "
                          f"${quote.ask * contracts * 100:.2f} over ${args.premium_cap} cap"}))
        return 0
    if contracts < 1:
        print(json.dumps({"ticker": ticker, "action": "skip",
                          "reason": f"ask {quote.ask} exceeds ${args.premium_cap} cap"}))
        return 0
    open_option_positions = 0
    try:
        from robinhood_tools.alpaca_paper import (  # noqa: E402
            AlpacaPaperBackend, AlpacaPaperHttpTransport)

        _backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        open_option_positions = sum(
            1 for p in _backend.list_positions()
            if len(str(p.get("symbol", ""))) > 10)
    except Exception:
        open_option_positions = 0
    if open_option_positions >= args.max_positions:
        print(json.dumps({"ticker": ticker, "action": "skip",
                          "reason": f"{open_option_positions} open option positions (max {args.max_positions})"}))
        return 0
    rec = {"ticker": ticker, "contract": quote.contract.option_symbol,
           "expiry": quote.contract.expiry, "strike": str(quote.contract.strike),
           "bid": str(quote.bid), "ask": str(quote.ask), "delta": str(quote.delta),
           "iv": str(quote.iv), "qty": contracts,
           "premium": str((Decimal(contracts) * quote.ask * 100).quantize(Decimal("0.01"))),
           "rejected_elsewhere": len(rejected)}
    # Odds gate verdict (owner decision 2026-11-16): the contract's
    # simulated EV under the guard's exit bands. Dry-run REPORTS it so the
    # operator can see the gate's opinion before --execute; execute mode
    # enforces it here (and place_long_option enforces it again at the
    # choke point, so both entry paths are covered).
    odds = _odds_verdict(quote, ticker, today, args)
    rec["odds"] = {
        "approved": odds.get("approved"),
        "ev_pct_on_premium": odds.get("ev_pct_on_premium"),
        "stop_first": odds.get("stop_first"),
        "neither": odds.get("neither"),
        "reason": odds.get("reason", ""),
    }
    if not odds.get("approved", False):
        print(json.dumps({"ticker": ticker, "action": "skip",
                          "contract": quote.contract.option_symbol,
                          "reason": odds.get("reason", "odds gate refused")}))
        return 0
    if args.dry_run or not args.execute:
        rec["action"] = "dry_run_buy"
        print(json.dumps(rec, indent=2))
        return 0
    from robinhood_tools.database import CioDatabase  # noqa: E402

    import uuid as _uuid

    candidate_id = f"{ticker}:{today.isoformat()}:long-{args.direction}:{_uuid.uuid4().hex[:8]}"
    db = CioDatabase(build_settings(args.config, args.env_file).database_path)
    db.record_candidate(candidate_id, ticker, "buy",
                        {"source": "options-scout", "contract": quote.contract.option_symbol,
                         "delta": str(quote.delta), "iv": str(quote.iv)})
    db.record_opinion(candidate_id, "scout", args.direction, 65,
                      {"contract": quote.contract.option_symbol,
                       "bid": str(quote.bid), "ask": str(quote.ask)})
    db.update_candidate_status(candidate_id, "risk_review")
    override = None
    if args.override_window:
        from datetime import datetime as _dt

        try:
            start, end = args.override_window.split("-")
            _dt.strptime(start, "%H:%M")
            _dt.strptime(end, "%H:%M")
            override = (start, end)
        except ValueError as exc:
            raise PolicyViolation(f"Bad --override-window: {exc}") from exc
    result = place_long_option(
        settings=settings, snapshot=snapshot, quote=quote, contracts=contracts,
        earnings_date=lookup_earnings_date(ticker), premium_cap=args.premium_cap,
        database=db, session_override=override,
        override_reason="owner-directed CLI execution" if override else "")
    db.update_candidate_status(candidate_id, "approved")
    rec.update({"action": "buy_placed", "order_id": result["order_id"],
                "status": result["status"], "approval_id": result["approval_id"],
                "alert": result.get("alert", "unknown"),
                "candidate_id": candidate_id})
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
