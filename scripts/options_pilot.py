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
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.errors import PolicyViolation  # noqa: E402
from robinhood_tools.options_flow import pick_long_option, place_long_option  # noqa: E402
from robinhood_tools.paper_flow import lookup_earnings_date  # noqa: E402
from robinhood_tools.paper_autopilot import build_evidence, record_attempt  # noqa: E402
from robinhood_tools.runtime import build_settings  # noqa: E402


EVIDENCE_MAX_AGE_MINUTES = {"quote": 5, "analysis": 15}


def _observed_at(value, *, field: str) -> datetime:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise PolicyViolation(f"missing {field} evidence timestamp")
    if timestamp.tzinfo is None:
        raise PolicyViolation(f"{field} evidence timestamp lacks timezone")
    return timestamp.astimezone(timezone.utc)


def collect_entry_evidence(
    symbol: str, side: str, quote: dict, analysis: dict, *, now: datetime
) -> dict:
    """Copy deterministic quote/indicator outputs into the entry snapshot."""
    if side not in {"call", "put"}:
        raise PolicyViolation("option side must be call or put")
    if now.tzinfo is None:
        raise PolicyViolation("now must include a timezone")
    quote_time = _observed_at(quote.get("as_of"), field="quote")
    analysis_time = _observed_at(analysis.get("as_of"), field="analysis")
    quote_payload = dict(quote)
    quote_payload["as_of"] = quote_time
    liquidity = {
        key: quote.get(key)
        for key in ("spread", "open_interest", "volume")
        if quote.get(key) is not None
    }
    return {
        "symbol": symbol.strip().upper(), "occ_symbol": quote.get("occ_symbol"),
        "side": side, "quantity": quote.get("quantity", 1), "quote": quote_payload,
        "premium": quote.get("reviewed_price", quote.get("ask")),
        "vwap": analysis.get("vwap"), "ema": analysis.get("ema"),
        "poc": analysis.get("poc"), "supply_demand": analysis.get("supply_demand"),
        "support_resistance": analysis.get("support_resistance"),
        "regime": analysis.get("regime"), "iv": quote.get("iv", analysis.get("iv")),
        "liquidity": liquidity, "earnings_date": analysis.get("earnings_date"),
        "session_status": analysis.get("session_status"),
        "position_status": analysis.get("position_status"),
        "policy_version": analysis.get("policy_version"),
        "freshness": {
            "quote": {"observed_at_utc": quote_time.isoformat(), "max_age_minutes": 5},
            "analysis": {"observed_at_utc": analysis_time.isoformat(), "max_age_minutes": 15},
        },
    }


def _evidence_block_reason(evidence: dict, *, now: datetime) -> str | None:
    required = (
        "symbol", "occ_symbol", "side", "quote", "vwap", "ema", "poc",
        "supply_demand", "support_resistance", "regime", "iv", "liquidity",
        "earnings_date", "session_status", "position_status", "policy_version", "freshness",
    )
    missing = [field for field in required if evidence.get(field) in (None, {}, "")]
    if missing:
        return "missing evidence: " + ", ".join(missing)
    for source, limit in EVIDENCE_MAX_AGE_MINUTES.items():
        try:
            observed = _observed_at(evidence["freshness"][source]["observed_at_utc"], field=source)
        except (KeyError, TypeError, ValueError, PolicyViolation):
            return f"missing {source} evidence timestamp"
        age = (now.astimezone(timezone.utc) - observed).total_seconds() / 60
        if age < 0 or age > limit:
            return f"stale {source} evidence ({age:.1f}m; limit {limit}m)"
    return None


def persist_candidate_before_gate(db_path, candidate: dict, *, gate, broker_call, now: datetime) -> dict:
    """Persist the screened candidate, then run gates, never the reverse."""
    evidence = build_evidence(candidate, now=now)
    reason = _evidence_block_reason(evidence, now=now)
    if reason:
        record_attempt(db_path, evidence, "blocked", reason)
        raise PolicyViolation(reason)
    record_attempt(db_path, evidence, "screened", "candidate screened")
    decision = gate()
    allowed, gate_reason = (decision, "gate failed") if isinstance(decision, bool) else decision
    if not allowed:
        record_attempt(db_path, evidence, "blocked", str(gate_reason))
        return {"action": "blocked", "reason": str(gate_reason), "candidate_id": evidence["candidate_id"]}
    order = broker_call()
    record_attempt(db_path, evidence, "accepted", "gates passed", order)
    return {"action": "accepted", "candidate_id": evidence["candidate_id"], "order": order}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Paper long-options pilot.")
    p.add_argument("--ticker", default="NVDA")
    p.add_argument("--direction", default="call", choices=["call", "put"])
    p.add_argument("--premium-cap", type=Decimal, default=Decimal("3000"))
    p.add_argument("--max-contracts", type=int, default=3)
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
    auto_qty = min(args.max_contracts, int(args.premium_cap // (quote.ask * 100)))
    contracts = args.qty if args.qty > 0 else auto_qty
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
                "candidate_id": candidate_id})
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
