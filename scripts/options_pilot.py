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
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.errors import PolicyViolation  # noqa: E402
from robinhood_tools.options_flow import pick_long_option, place_long_option  # noqa: E402
from robinhood_tools.paper_flow import lookup_earnings_date  # noqa: E402
from robinhood_tools.paper_autopilot import (  # noqa: E402
    build_evidence, claim_candidate, record_attempt, write_evidence,
)
from robinhood_tools.runtime import build_settings  # noqa: E402


EVIDENCE_MAX_AGE_MINUTES = {"quote": 5, "analysis": 15}
_BLOCKED_EVIDENCE_FIELDS = frozenset({
    "symbol", "occ_symbol", "side", "quantity", "quote", "premium", "vwap", "ema",
    "poc", "supply_demand", "support_resistance", "regime", "iv", "liquidity",
    "earnings_date", "earnings_provenance", "analysis_source", "session_status",
    "position_status", "policy_version", "freshness",
})


def _annotate_blocked_evidence(evidence: dict, *, failure_stage: str, reason: str) -> None:
    known_fields = sorted(
        field for field in _BLOCKED_EVIDENCE_FIELDS
        if evidence.get(field) not in (None, {}, "")
    )
    evidence.update({
        "evidence_type": "blocked",
        "failure_stage": failure_stage,
        "known_fields": known_fields,
        "unknown_fields": sorted(_BLOCKED_EVIDENCE_FIELDS - set(known_fields)),
        "reason": reason,
    })


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
    try:
        quote_time = _observed_at(quote.get("as_of"), field="quote")
    except (PolicyViolation, TypeError, ValueError):
        quote_time = None
    try:
        analysis_time = _observed_at(analysis.get("as_of"), field="analysis")
    except (PolicyViolation, TypeError, ValueError):
        analysis_time = None
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
        "earnings_provenance": analysis.get("earnings_provenance"),
        "analysis_source": analysis.get("source"),
        "session_status": analysis.get("session_status"),
        "position_status": analysis.get("position_status"),
        "policy_version": analysis.get("policy_version"),
        "freshness": {
            "quote": {"observed_at_utc": quote_time.isoformat() if quote_time else None, "max_age_minutes": 5},
            "analysis": {"observed_at_utc": analysis_time.isoformat() if analysis_time else None, "max_age_minutes": 15},
        },
    }


def _positive_number(value) -> bool:
    try:
        number = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return False
    return number.is_finite() and number > 0


def _evidence_block_reason(
    evidence: dict, *, now: datetime, active_policy_version: str = "paper-options-v1",
    freshness_limits: dict[str, int] | None = None,
) -> str | None:
    limits = {**EVIDENCE_MAX_AGE_MINUTES, **(freshness_limits or {})}
    required = (
        "symbol", "occ_symbol", "side", "quote", "vwap", "ema", "poc",
        "supply_demand", "support_resistance", "regime", "iv", "liquidity",
        "earnings_date", "earnings_provenance", "session_status", "position_status",
        "policy_version", "freshness",
    )
    missing = [field for field in required if evidence.get(field) in (None, {}, "")]
    if missing:
        return "missing evidence: " + ", ".join(missing)
    quote = evidence["quote"]
    if not isinstance(quote, dict):
        return "invalid quote evidence: quote must be an object"
    if quote.get("source") not in {"alpaca-options"}:
        return "invalid quote source"
    if evidence.get("analysis_source") not in {"alpaca-bars", "deterministic-bars"}:
        return "invalid analysis source"
    if not isinstance(quote, dict) or not all(_positive_number(quote.get(key)) for key in ("bid", "ask", "reviewed_price")):
        return "invalid quote evidence: bid, ask, and reviewed price must be positive"
    if quote["bid"] > quote["ask"]:
        return "invalid quote evidence: bid exceeds ask"
    quantity = evidence.get("quantity")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        return "invalid quantity evidence"
    if quote.get("quantity") is not None and quote.get("quantity") != quantity:
        return "invalid quantity evidence: quote quantity mismatch"
    occ = str(evidence.get("occ_symbol") or "")
    match = re.fullmatch(r"([A-Z0-9.]{1,6})(\d{6})([CP])(\d{8})", occ)
    if not match:
        return "invalid OCC symbol"
    if match.group(1) != str(evidence["symbol"]).upper():
        return "invalid OCC underlying"
    expected_right = "C" if evidence["side"] == "call" else "P"
    if match.group(3) != expected_right:
        return "invalid OCC right"
    if not _positive_number(evidence["iv"]):
        return "invalid IV evidence"
    liquidity = evidence["liquidity"]
    if not isinstance(liquidity, dict) or not all(_positive_number(liquidity.get(key)) for key in ("spread", "volume", "open_interest")):
        return "invalid liquidity evidence"
    if not _positive_number(evidence["vwap"]) or not _positive_number(evidence["poc"]):
        return "invalid indicator evidence: VWAP and POC must be positive"
    ema = evidence["ema"]
    if not isinstance(ema, dict) or not ema or not all(_positive_number(value) for value in ema.values()):
        return "invalid indicator evidence: EMA values must be positive"
    for field in ("supply_demand", "support_resistance"):
        levels = evidence[field]
        if not isinstance(levels, dict) or not levels or not all(
            isinstance(values, (list, tuple)) and values and all(_positive_number(value) for value in values)
            for values in levels.values()
        ):
            return f"invalid indicator evidence: {field} levels"
    try:
        datetime.fromisoformat(str(evidence["earnings_date"]))
    except ValueError:
        try:
            datetime.strptime(str(evidence["earnings_date"]), "%Y-%m-%d")
        except ValueError:
            return "invalid earnings date evidence"
    if str(evidence["earnings_provenance"]) not in {"SEC/company-calendar", "paper_flow.lookup_earnings_date"}:
        return "invalid earnings provenance evidence"
    if evidence["policy_version"] != active_policy_version:
        return f"policy version mismatch: expected {active_policy_version}"
    for source, limit in limits.items():
        try:
            observed = _observed_at(evidence["freshness"][source]["observed_at_utc"], field=source)
        except (KeyError, TypeError, ValueError, PolicyViolation):
            return f"missing {source} evidence timestamp"
        age = (now.astimezone(timezone.utc) - observed).total_seconds() / 60
        if age < 0 or age > limit:
            return f"stale {source} evidence ({age:.1f}m; limit {limit}m)"
    return None


def persist_candidate_before_gate(
    db_path, candidate: dict, *, gate, broker_call, now: datetime,
    evidence_root: Path | None = None, active_policy_version: str = "paper-options-v1",
    freshness_limits: dict[str, int] | None = None,
) -> dict:
    """Persist the screened candidate, then run gates, never the reverse."""
    evidence = build_evidence(candidate, now=now)
    root = Path(evidence_root) if evidence_root is not None else Path(db_path).parent
    reason = _evidence_block_reason(
        evidence, now=now, active_policy_version=active_policy_version,
        freshness_limits=freshness_limits,
    )
    if reason:
        _annotate_blocked_evidence(evidence, failure_stage="validation", reason=reason)
        evidence["action"] = "blocked"
        record_attempt(db_path, evidence, "blocked", reason)
        write_evidence(root, evidence)
        raise PolicyViolation(reason)
    if not claim_candidate(db_path, evidence):
        reason = "duplicate candidate or OCC claim"
        _annotate_blocked_evidence(evidence, failure_stage="claim", reason=reason)
        evidence["action"] = "blocked"
        record_attempt(db_path, evidence, "blocked", reason)
        write_evidence(root, evidence)
        return {"action": "blocked", "reason": reason, "candidate_id": evidence["candidate_id"]}
    evidence.update({"action": "screened", "reason": "candidate screened"})
    record_attempt(db_path, evidence, "screened", "candidate screened")
    write_evidence(root, evidence)
    try:
        decision = gate()
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
        _annotate_blocked_evidence(evidence, failure_stage="gate", reason=reason)
        evidence["action"] = "blocked"
        record_attempt(db_path, evidence, "blocked", reason)
        write_evidence(root, evidence)
        raise
    allowed, gate_reason = (decision, "gate failed") if isinstance(decision, bool) else decision
    if not allowed:
        reason = str(gate_reason)
        _annotate_blocked_evidence(evidence, failure_stage="gate", reason=reason)
        evidence["action"] = "blocked"
        record_attempt(db_path, evidence, "blocked", reason)
        write_evidence(root, evidence)
        return {"action": "blocked", "reason": reason, "candidate_id": evidence["candidate_id"]}
    try:
        order = broker_call()
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
        _annotate_blocked_evidence(evidence, failure_stage="broker", reason=reason)
        evidence["action"] = "blocked"
        record_attempt(db_path, evidence, "blocked", reason)
        write_evidence(root, evidence)
        raise
    evidence.update({"action": "accepted", "reason": "gates passed"})
    record_attempt(db_path, evidence, "accepted", "gates passed", order)
    write_evidence(root, evidence)
    return {"action": "accepted", "candidate_id": evidence["candidate_id"], "order": order}


def record_preentry_block(
    db_path, symbol: str, side: str, quote: dict | None, analysis: dict | None,
    *, reason: str, failure_stage: str = "pre_entry", now: datetime,
    evidence_root: Path | None = None,
) -> dict:
    """Record one early skip/failure before selection or the order gate."""
    quote = quote or {}
    analysis = analysis or {}
    candidate = collect_entry_evidence(symbol, side, quote, analysis, now=now)
    evidence = build_evidence(candidate, now=now)
    fields = {
        "symbol", "occ_symbol", "side", "quantity", "quote", "premium", "vwap", "ema",
        "poc", "supply_demand", "support_resistance", "regime", "iv", "liquidity",
        "earnings_date", "earnings_provenance", "analysis_source", "session_status",
        "position_status", "policy_version", "freshness",
    }
    known_fields = sorted(field for field in fields if evidence.get(field) not in (None, {}, ""))
    unknown_fields = sorted(fields - set(known_fields))
    evidence.update({
        "evidence_type": "blocked",
        "failure_stage": failure_stage,
        "known_fields": known_fields,
        "unknown_fields": unknown_fields,
    })
    from robinhood_tools.paper_autopilot import attempt_exists
    if attempt_exists(db_path, evidence, "blocked", reason):
        return {"action": "blocked", "duplicate": True, "reason": reason}
    evidence.update({"action": "blocked", "reason": reason})
    root = Path(evidence_root) if evidence_root is not None else Path(db_path).parent
    record_attempt(db_path, evidence, "blocked", reason)
    write_evidence(root, evidence)
    return {"action": "blocked", "reason": reason, "candidate_id": evidence["candidate_id"]}


def persist_rejected_chain_candidates(
    db_path, symbol: str, side: str, rejected, *, now: datetime,
    evidence_root: Path | None = None,
) -> None:
    """Persist each deterministic chain rejection without claiming it for entry."""
    root = Path(evidence_root) if evidence_root is not None else Path(db_path).parent
    for rejection in rejected:
        reasons = list(getattr(rejection, "reasons", ()))
        candidate = {
            "symbol": symbol.strip().upper(),
            "occ_symbol": rejection.option_symbol,
            "side": side,
            "quote": {"occ_symbol": rejection.option_symbol, "source": "alpaca-options"},
            "failure_stage": "chain_selection",
            "known_fields": ["symbol", "occ_symbol", "side", "quote", "rejection_reasons"],
            "unknown_fields": ["technical_analysis", "earnings", "position", "gate_results"],
            "evidence_type": "rejected_candidate",
        }
        evidence = build_evidence(candidate, now=now)
        evidence["rejection_reasons"] = reasons
        reason = "selection rejected: " + "; ".join(reasons)
        evidence.update({"action": "screened", "status": "screened", "reason": reason})
        record_attempt(db_path, evidence, "screened", reason)
        write_evidence(root, evidence)


def _option_quote_payload(quote, *, quantity: int = 1) -> dict:
    return {
        "occ_symbol": quote.contract.option_symbol, "bid": quote.bid,
        "ask": quote.ask, "reviewed_price": quote.ask, "as_of": quote.quoted_at,
        "source": "alpaca-options", "iv": quote.iv,
        "open_interest": quote.open_interest, "volume": quote.volume,
        "spread": quote.ask - quote.bid, "quantity": quantity,
    }


def collect_market_analysis(symbol: str, market_data, *, now: datetime, earnings_date) -> dict:
    """Build technical evidence from read-only bars and existing math helpers."""
    from robinhood_tools.market_prices import market_regime
    from robinhood_tools.signal_engine import ema

    start = (now.astimezone(timezone.utc) - timedelta(days=5)).isoformat()
    bars = market_data.stock_bars(symbol, timeframe="5Min", start=start, limit=1000)
    spy_bars = market_data.stock_bars("SPY", timeframe="5Min", start=start, limit=1000)
    if len(bars) < 50 or len(spy_bars) < 51:
        raise PolicyViolation("insufficient fresh bars for technical evidence")
    closes = [float(item["c"]) for item in bars]
    highs = [float(item["h"]) for item in bars]
    lows = [float(item["l"]) for item in bars]
    volumes = [float(item["v"]) for item in bars]
    recent = list(zip(highs[-20:], lows[-20:], closes[-20:], volumes[-20:]))
    vwap = sum(((high + low + close) / 3) * volume for high, low, close, volume in recent) / max(sum(item[3] for item in recent), 1e-9)
    volume_peak = max(recent, key=lambda item: item[3])
    return {
        "as_of": bars[-1].get("t"),
        "source": "alpaca-bars",
        "vwap": vwap,
        "ema": {"ema_9": ema(closes, 9)[-1], "ema_21": ema(closes, 21)[-1]},
        "poc": volume_peak[2],
        "supply_demand": {"supply": [max(highs[-20:])], "demand": [min(lows[-20:])]},
        "support_resistance": {"support": [min(lows[-20:])], "resistance": [max(highs[-20:])]},
        "regime": market_regime([float(item["c"]) for item in spy_bars]),
        "earnings_date": earnings_date,
        "earnings_provenance": "paper_flow.lookup_earnings_date",
        "session_status": "OPEN",
        "position_status": "unknown",
        "policy_version": "paper-options-v1",
    }


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
    ticker = args.ticker.strip().upper()
    now = datetime.now(timezone.utc)
    block_db_path = Path("outputs/paper/cio.db")

    def record_block(reason: str, quote_payload: dict | None = None,
                     analysis: dict | None = None, *, db_path: Path | None = None) -> None:
        target = db_path or block_db_path
        record_preentry_block(
            target, ticker, args.direction, quote_payload or {}, analysis or {},
            reason=reason, now=now, evidence_root=target.parent,
        )

    try:
        settings = build_settings(args.config, args.env_file)
        block_db_path = settings.database_path
    except Exception as exc:
        record_block(f"setup failure: {exc}")
        raise
    if settings.trading_enabled:
        print("REFUSAL: live kill switch open.", file=sys.stderr)
        record_block("live trading kill switch is open")
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

    try:
        env = {**load_env(args.env_file), **os.environ}
    except Exception as exc:
        record_block(f"environment setup failure: {exc}")
        raise
    today = now.date()

    if args.close:
        print(json.dumps({"mode": "close", "action": "manual-close",
                          "note": "use the desk guard/exit path; closes travel review+ledger"}))
        return 0

    try:
        data = AlpacaOptionsData.from_values(env)
        market_data = AlpacaMarketDataHttpClient.from_values(env)
        spot_q = get_quote(ticker, data_client=market_data,
                           max_age_minutes=args.max_age_minutes)
    except Exception as exc:
        record_block(f"market-data setup/lookup failure: {exc}")
        raise
    spot = spot_q.price
    print(f"{ticker} spot {spot} ({spot_q.source} {spot_q.as_of})")

    try:
        req = urllib.request.Request("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                                     headers={"User-Agent": "desk-options/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            page = resp.read().decode("utf-8", "replace")
        m = re.search(r"List of S&amp;P 500 companies.*?(<table.*?</table>)", page, re.S)
        table = m.group(1) if m else page
        members = {s.upper().replace(".", "-") for s in re.findall(r">([A-Z]{1,5}(?:\.[A-Z])?)<", table)}
        snapshot = Sp500Snapshot(symbols=frozenset(members),
                                 as_of=now.isoformat(),
                                 source_url="https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    except Exception as exc:
        record_block(f"universe lookup failure: {exc}", {
            "as_of": spot_q.as_of, "source": "alpaca-options",
            "bid": spot_q.price, "ask": spot_q.price,
            "reviewed_price": spot_q.price,
        })
        raise

    try:
        quote, rejected = pick_long_option(
            data=data, underlying=ticker, spot=spot,
            direction="bullish" if args.direction == "call" else "bearish", today=today)
    except Exception as exc:
        rejected = getattr(exc, "rejected", ())
        if rejected:
            persist_rejected_chain_candidates(
                settings.database_path, ticker, args.direction, rejected,
                now=now, evidence_root=settings.database_path.parent,
            )
        record_block(f"option selection failure: {exc}")
        raise
    persist_rejected_chain_candidates(
        settings.database_path, ticker, args.direction, rejected,
        now=now, evidence_root=settings.database_path.parent,
    )
    auto_qty = min(args.max_contracts, int(args.premium_cap // (quote.ask * 100)))
    contracts = args.qty if args.qty > 0 else auto_qty
    quote_payload = _option_quote_payload(quote, quantity=contracts if contracts > 0 else 1)
    if args.qty > args.max_contracts:
        reason = f"directed qty {args.qty} exceeds max contracts {args.max_contracts}"
        print(json.dumps({"ticker": ticker, "action": "skip", "reason": reason}))
        record_block(reason, quote_payload)
        return 0
    if args.qty > 0 and quote.ask * contracts * 100 > args.premium_cap:
        reason = (f"directed qty {contracts} costs "
                  f"${quote.ask * contracts * 100:.2f} over ${args.premium_cap} cap")
        print(json.dumps({"ticker": ticker, "action": "skip", "reason": reason}))
        record_block(reason, quote_payload)
        return 0
    if contracts < 1:
        reason = f"ask {quote.ask} exceeds ${args.premium_cap} cap"
        print(json.dumps({"ticker": ticker, "action": "skip", "reason": reason}))
        record_block(reason, _option_quote_payload(quote, quantity=1))
        return 0
    open_option_positions = 0
    try:
        from robinhood_tools.alpaca_paper import (  # noqa: E402
            AlpacaPaperBackend, AlpacaPaperHttpTransport)

        _backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        open_option_positions = sum(
            1 for p in _backend.list_positions()
            if len(str(p.get("symbol", ""))) > 10)
    except Exception as exc:
        reason = f"position lookup failure: {exc}"
        print(json.dumps({"ticker": ticker, "action": "skip", "reason": reason}))
        record_block(reason, quote_payload)
        return 0
    if open_option_positions >= args.max_positions:
        reason = f"{open_option_positions} open option positions (max {args.max_positions})"
        print(json.dumps({"ticker": ticker, "action": "skip", "reason": reason}))
        record_block(reason, quote_payload)
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
    try:
        db = CioDatabase(settings.database_path)
    except Exception as exc:
        record_block(f"paper database setup failure: {exc}")
        raise
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
            reason = f"Bad --override-window: {exc}"
            record_block(reason, quote_payload)
            raise PolicyViolation(reason) from exc
    try:
        earnings_date = lookup_earnings_date(ticker)
    except Exception as exc:
        record_block(
            f"earnings lookup failure: {exc}", quote_payload,
            {"as_of": now, "source": "alpaca-bars"},
        )
        raise
    try:
        analysis = collect_market_analysis(
            ticker, market_data, now=now, earnings_date=earnings_date)
    except Exception as exc:
        # Preserve a durable blocked attempt when technical collection itself
        # cannot complete; never synthesize an indicator or bypass the gate.
        analysis = {
            "as_of": now, "earnings_date": earnings_date,
            "earnings_provenance": "paper_flow.lookup_earnings_date",
            "session_status": "OPEN", "position_status": "unknown",
            "policy_version": "paper-options-v1", "collection_error": str(exc),
        }
    option_payload = _option_quote_payload(quote, quantity=contracts)
    candidate = collect_entry_evidence(
        ticker, args.direction, option_payload, analysis, now=now)
    result = persist_candidate_before_gate(
        settings.database_path, candidate, gate=lambda: True,
        broker_call=lambda: place_long_option(
            settings=settings, snapshot=snapshot, quote=quote, contracts=contracts,
            earnings_date=earnings_date, premium_cap=args.premium_cap,
            database=db, session_override=override,
            override_reason="owner-directed CLI execution" if override else ""),
        now=now, evidence_root=settings.database_path.parent,
        active_policy_version="paper-options-v1",
        freshness_limits={"quote": args.max_age_minutes,
                          "analysis": settings.freshness_max_age_minutes.get("market_regime", 30)},
    )
    order_result = result["order"]
    db.update_candidate_status(candidate_id, "approved")
    rec.update({"action": "buy_placed", "order_id": order_result["order_id"],
                "status": order_result["status"], "approval_id": order_result["approval_id"],
                "candidate_id": candidate_id})
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
