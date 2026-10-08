"""Autonomous paper trader: TradingAgents research -> Alpaca paper orders.

Paper-only. Never touches Robinhood live or Alpaca live.
- Live kill switch stays off: refuses unless mode=paper_auto,
  PAPER_TRADING_ENABLED=true, TRADING_ENABLED=false, broker=alpaca paper.
- Session gate enforced by the paper service execution guard
  (10:15-15:30 ET weekdays + Alpaca market clock open). Outside the
  window it researches and exits with a skip reason, no order.
- Per ticker: TradingAgents (default nvidia/kimi-k3, proven working) ->
  Buy/Overweight => buy, Sell/Underweight => sell held position, else hold.
- Gates per buy: S&P500 member (<24h Wikipedia snapshot), earnings >5d
  away (yfinance, fail-closed), $500 order/symbol cap, buying-power check,
  DAY limit at current price, Alpaca review fingerprint match,
  approval ledger + atomic reservation via CioDatabase.
- Records JSON to outputs/paper/tradingagents/<TICKER>/<DATE>/auto.json.

Run with the TradingAgents venv (has yfinance + LLM clients):
  ../TradingAgents/.venv/bin/python scripts/tradingagents_paper_auto.py --tickers NVDA --dry-run
  ../TradingAgents/.venv/bin/python scripts/tradingagents_paper_auto.py --tickers NVDA

Schedule: weekdays 10:20 ET after the 10:15 window opens (Codex automation
or launchd). See docs/approval_automation.md for the existing 08:45/10:15 setup.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.errors import PolicyViolation  # noqa: E402
from robinhood_tools.paper_flow import (SignalOrder, lookup_earnings_date,  # noqa: E402
                                        place_signal_order)
from robinhood_tools.runtime import build_settings  # noqa: E402
from robinhood_tools.settings import load_env  # noqa: E402
from robinhood_tools.universe import Sp500Snapshot  # noqa: E402

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
BUY_SIGNALS = {"buy", "overweight"}
SELL_SIGNALS = {"sell", "underweight"}
ET = ZoneInfo("America/New_York")
STATUS_FILE = Path("outputs/paper/tradingagents/status.json")
RUN_LOG = Path("outputs/paper/tradingagents/live.log")


def set_stage(ticker: str, stage: str, detail: str = "") -> None:
    """Write live progress for the UI (best effort, never raises)."""
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        current = {}
        if STATUS_FILE.exists():
            current = json.loads(STATUS_FILE.read_text())
        current[ticker.upper()] = {
            "stage": stage, "detail": detail[:300],
            "updated_at": datetime.now(ET).isoformat(),
        }
        STATUS_FILE.write_text(json.dumps(current, indent=2))
    except Exception:
        pass


def log_line(message: str) -> None:
    print(message, flush=True)
    try:
        RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(RUN_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(ET).isoformat()} {message}\n")
    except Exception:
        pass


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Autonomous TradingAgents -> Alpaca paper trader.")
    p.add_argument("--tickers", default="NVDA", help="comma list, e.g. NVDA,AAPL,MSFT")
    p.add_argument("--options", action="store_true",
                   help="trade long calls/puts instead of stock (paper-authorized)")
    p.add_argument("--premium-cap", type=Decimal, default=Decimal("250"))
    p.add_argument("--max-option-positions", type=int, default=1)
    p.add_argument("--screen", default="",
                   help="screen JSON (outputs/screener/latest.json); overrides --tickers with top-N")
    p.add_argument("--screen-top", type=int, default=2,
                   help="how many screened tickers to trade (bounds LLM cost)")
    p.add_argument("--date", default=None, help="analysis date YYYY-MM-DD, default today ET")
    p.add_argument("--provider", default="nvidia")
    p.add_argument("--quick-model", default="moonshotai/kimi-k3")
    p.add_argument("--deep-model", default="moonshotai/kimi-k3")
    p.add_argument("--analysts", default="market,news,fundamentals")
    p.add_argument("--max-notional", type=Decimal, default=Decimal("500"))
    p.add_argument("--stop-pct", type=Decimal, default=Decimal("0.08"),
                   help="bracket stop below entry (0 to disable brackets)")
    p.add_argument("--target-pct", type=Decimal, default=Decimal("0.20"),
                   help="bracket target above entry")
    p.add_argument("--dry-run", action="store_true", help="research + gates only, never place")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def fetch_sp500() -> Sp500Snapshot:
    req = urllib.request.Request(SP500_URL, headers={"User-Agent": "robinhood-trading-tools paper/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        html_text = resp.read().decode("utf-8", "replace")
    # Wikipedia constituents table: first column links like >NVDA< / >AAPL<
    symbols = {s.upper() for s in re.findall(r">([A-Z]{1,5}(?:\.[A-Z])?)<", html_text)}
    # Keep only plausible tickers that appear in the constituents table region
    table_match = re.search(r"List of S&amp;P 500 companies.*?(<table.*?</table>)", html_text, re.S)
    if table_match:
        table = table_match.group(1)
        symbols = {s.upper() for s in re.findall(r">([A-Z]{1,5}(?:\.[A-Z])?)<", table)}
    symbols = {s.replace(".", "-") for s in symbols if 1 <= len(s) <= 6}
    if len(symbols) < 400:
        raise PolicyViolation(f"S&P 500 fetch returned only {len(symbols)} symbols; failing closed.")
    return Sp500Snapshot(
        symbols=frozenset(symbols),
        as_of=datetime.now(timezone.utc).isoformat(),
        source_url=SP500_URL,
    )


def current_quote(symbol: str, data_client=None) -> tuple[Decimal, str, str]:
    """Authoritative quote: Alpaca snapshot first, yfinance fallback.

    Returns (price, source, as_of). Stale quotes fail closed.
    """
    from robinhood_tools.market_prices import get_quote  # noqa: E402

    quote = get_quote(symbol, data_client=data_client, max_age_minutes=5)
    return quote.price, quote.source, quote.as_of


def run_research(ticker: str, trade_date: str, args) -> tuple[str, dict]:
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = args.provider
    config["quick_think_llm"] = args.quick_model
    config["deep_think_llm"] = args.deep_model
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    if config.get("llm_max_retries") is None:
        config["llm_max_retries"] = 6
    if config.get("max_tool_rounds", 20) > 10:
        config["max_tool_rounds"] = 10
    selected = [a.strip() for a in args.analysts.split(",") if a.strip()]
    ta = TradingAgentsGraph(selected_analysts=selected, debug=False, config=config)
    state, decision = ta.propagate(ticker, trade_date)
    reports = {}
    if isinstance(state, dict):
        for key, value in state.items():
            if key.endswith("_report") and isinstance(value, str) and value.strip():
                reports[key] = value.strip()[:2000]
    try:
        ta.save_reports(state, ticker)
    except Exception:
        pass
    return str(decision), {
        "provider": args.provider,
        "quick_model": args.quick_model,
        "deep_model": args.deep_model,
        "analysts": selected,
        "analyst_reports": reports,
    }


def trade_option_leg(*, ticker: str, sig: str, decision: str, trade_date: str,
                     settings, snapshot, env, args, rec: dict,
                     data_client) -> None:
    """Debate direction -> long call/put via the gated options flow.

    Mutates rec in place. Raises PolicyViolation on any gate failure.
    Execution authority stays with place_long_option (review + ledger +
    session guard); this function only scouts, sizes, and records.
    """
    from datetime import date as _date

    from robinhood_tools.alpaca_options import AlpacaOptionsData
    from robinhood_tools.database import CioDatabase
    from robinhood_tools.market_prices import get_quote
    from robinhood_tools.options_flow import pick_long_option, place_long_option
    from robinhood_tools.paper_flow import lookup_earnings_date

    direction = "bullish" if sig in BUY_SIGNALS else "bearish"
    set_stage(ticker, "scouting", f"{direction} chain for {ticker}")
    data = AlpacaOptionsData.from_values(env)
    spot_q = get_quote(ticker, data_client=data_client)
    quote, rejected = pick_long_option(
        data=data, underlying=ticker, spot=spot_q.price,
        direction=direction, today=_date.today())
    rec.update({"spot": str(spot_q.price), "spot_source": spot_q.source,
                "contract": quote.contract.option_symbol,
                "expiry": quote.contract.expiry, "strike": str(quote.contract.strike),
                "bid": str(quote.bid), "ask": str(quote.ask),
                "delta": str(quote.delta), "iv": str(quote.iv),
                "rejected_elsewhere": len(rejected)})
    contracts = int(args.premium_cap // (quote.ask * 100))
    if contracts < 1:
        rec["reason"] = f"ask {quote.ask} exceeds ${args.premium_cap} cap"
        set_stage(ticker, "skip", rec["reason"])
        return
    open_opts = 0
    try:
        from robinhood_tools.alpaca_paper import (  # noqa: E402
            AlpacaPaperBackend, AlpacaPaperHttpTransport)

        _backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        open_opts = sum(1 for p in _backend.list_positions()
                        if len(str(p.get("symbol", ""))) > 10)
    except Exception:
        open_opts = 0
    if open_opts >= args.max_option_positions:
        rec["reason"] = (f"{open_opts} open option positions "
                         f"(max {args.max_option_positions})")
        set_stage(ticker, "skip", rec["reason"])
        return
    premium = (quote.ask * contracts * 100).quantize(Decimal("0.01"))
    if args.dry_run:
        rec.update({"action": "dry_run_buy", "qty": contracts,
                    "premium": str(premium)})
        set_stage(ticker, "dry_run", f"{contracts}x{quote.contract.option_symbol}")
        return
    if args.dry_run:
        rec.update({"action": "dry_run_buy", "qty": contracts,
                    "premium": str((quote.ask * contracts * 100).quantize(Decimal("0.01")))})
        return
    db = CioDatabase(settings.database_path)
    candidate_id = f"{ticker}:{trade_date}:long-{direction}:{__import__('uuid').uuid4().hex[:8]}"
    db.record_candidate(candidate_id, ticker, "buy",
                        {"source": "options-auto", "contract": quote.contract.option_symbol,
                         "signal": decision})
    db.record_opinion(candidate_id, "scout", direction, 65,
                      {"contract": quote.contract.option_symbol,
                       "bid": str(quote.bid), "ask": str(quote.ask)})
    db.update_candidate_status(candidate_id, "risk_review")
    result = place_long_option(
        settings=settings, snapshot=snapshot, quote=quote, contracts=contracts,
        earnings_date=lookup_earnings_date(ticker), premium_cap=args.premium_cap,
        database=db)
    db.update_candidate_status(candidate_id, "approved")
    rec.update({"action": "buy_placed", "qty": contracts,
                "premium": result["premium"], "order_id": result["order_id"],
                "status": result["status"], "approval_id": result["approval_id"],
                "candidate_id": candidate_id})
    set_stage(ticker, "placing", f"approval {result['approval_id']}")
    log_line(f"[{ticker}] PLACED long {direction} {contracts}x{quote.contract.option_symbol} "
             f"status={result['status']} order={result['order_id']}")


def main(argv=None) -> int:
    args = parse_args(argv)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.screen:
        screen_data = json.loads(Path(args.screen).read_text())
        tickers = [r["symbol"].upper().replace(".", "-")
                   for r in screen_data.get("ranked", [])[:args.screen_top]]
        print(f"screen {args.screen}: top {args.screen_top} -> {tickers}")
        if not tickers:
            print("screen produced no tickers; nothing to do.")
            return 0
    today_et = datetime.now(ET).date().isoformat()
    trade_date = args.date or today_et

    settings = build_settings(args.config, args.env_file)
    if settings.trading_enabled:
        print("REFUSAL: TRADING_ENABLED=true (live kill switch open). Paper auto refuses to run.", file=sys.stderr)
        return 2
    try:
        settings.require_paper_trading()
    except PolicyViolation as exc:
        print(f"REFUSAL: {exc}", file=sys.stderr)
        return 2
    if not settings.paper_trading_enabled or not settings.paper_autonomy.enabled:
        print("REFUSAL: paper trading or paper autonomy disabled.", file=sys.stderr)
        return 2

    env = {**load_env(args.env_file), **__import__("os").environ}
    snapshot = fetch_sp500()
    print(f"S&P500 snapshot: {len(snapshot.symbols)} symbols as of {snapshot.as_of}")

    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport

    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    try:
        from robinhood_tools.alpaca_market_data import AlpacaMarketDataHttpClient  # noqa: E402

        data_client = AlpacaMarketDataHttpClient.from_values(env)
    except Exception:
        data_client = None
    accounts = backend.list_accounts()
    clock = backend.market_clock()
    print(f"Alpaca paper account {accounts[0].masked_account_number}, market open: {clock.get('is_open')}")

    try:
        acct = backend.transport.request("GET", "/v2/account")
        buying_power = Decimal(str(acct.get("buying_power", "0")))
    except Exception:
        buying_power = Decimal("0")

    positions = {p.get("symbol", "").upper(): Decimal(str(p.get("qty", "0"))) for p in backend.list_positions()}

    results = []
    for ticker in tickers:
        rec: dict = {"ticker": ticker, "date": trade_date, "action": "skip", "reason": ""}
        set_stage(ticker, "researching", "TradingAgents analysts running")
        log_line(f"[{ticker}] researching ({trade_date}) via {args.provider}/{args.quick_model}")
        try:
            import dataclasses as _dc

            _snap = snapshot
            if settings.index_etf_allowlist:
                _snap = _dc.replace(snapshot, index_etfs=frozenset(settings.index_etf_allowlist))
            _snap.require_eligible_purchase(ticker)
            set_stage(ticker, "researching", "S&P500 OK, LLM debate running")
            decision, meta = run_research(ticker, trade_date, args)
            rec.update(meta)
            rec["decision"] = decision
            log_line(f"[{ticker}] decision: {decision}")
            sig = decision.strip().lower()
            set_stage(ticker, "debating", f"decision={decision}, recording desk debate")
            try:
                import uuid as _uuid  # noqa: E402

                from robinhood_tools.agents import (  # noqa: E402
                    desk_input_from_signal, run_desk_analysis)
                from robinhood_tools.database import CioDatabase  # noqa: E402

                advisory = run_desk_analysis(
                    database=CioDatabase(settings.database_path),
                    candidate_id=f"{ticker}:{trade_date}:signal:{_uuid.uuid4().hex[:8]}",
                    desk_input=desk_input_from_signal(
                        ticker, decision,
                        f"{args.provider}/{args.quick_model}",
                        analyst_reports=meta.get("analyst_reports")))
                rec["desk_verdict"] = advisory["judgment"].verdict
                rec["desk_outcome"] = advisory["outcome"]
                log_line(f"[{ticker}] desk: {advisory['judgment'].verdict} "
                         f"({advisory['outcome']})")
                desk_agrees = (
                    (sig in BUY_SIGNALS and advisory["outcome"] in
                     {"ADVISORY_CALL", "ADVISORY_STRONG_CALL"})
                    or (sig in SELL_SIGNALS and advisory["outcome"] in
                        {"ADVISORY_PUT", "ADVISORY_STRONG_PUT"}))
            except Exception as exc:  # noqa: BLE001 - record failure blocks (fail closed)
                desk_agrees = False
                rec["desk_outcome"] = f"record-failed: {type(exc).__name__}"
                log_line(f"[{ticker}] desk record failed ({exc}); blocking trade")
            if sig not in BUY_SIGNALS and sig not in SELL_SIGNALS:
                rec["reason"] = f"non-actionable decision ({decision})"
                set_stage(ticker, "skip", rec["reason"])
            elif not desk_agrees:
                rec["reason"] = (f"desk disagrees: signal {decision} vs "
                                 f"{rec.get('desk_verdict')} ({rec.get('desk_outcome')})")
                set_stage(ticker, "skip", rec["reason"])
            else:
                set_stage(ticker, "gating", f"decision={decision}, checking price/earnings/risk")
                if args.options:
                    trade_option_leg(ticker=ticker, sig=sig, decision=decision,
                                     trade_date=trade_date, settings=settings,
                                     snapshot=snapshot, env=env, args=args, rec=rec,
                                     data_client=data_client)
                else:
                    price, price_source, price_as_of = current_quote(ticker, data_client)
                    rec["price"] = str(price)
                    rec["price_source"] = price_source
                    rec["price_as_of"] = price_as_of
                    cap = min(args.max_notional, settings.risk_limits.max_order_value)
                    if sig in BUY_SIGNALS:
                        earnings = lookup_earnings_date(ticker)
                        qty = int((cap // price)) if price > 0 else 0
                        limit = price.quantize(Decimal("0.01"))
                        stop_px, target_px = None, None
                        if args.stop_pct and args.stop_pct > 0 and args.target_pct and args.target_pct > 0:
                            stop_px = (limit * (1 - args.stop_pct)).quantize(Decimal("0.01"))
                            target_px = (limit * (1 + args.target_pct)).quantize(Decimal("0.01"))
                        if qty < 1:
                            rec["reason"] = f"price {price} exceeds ${cap} cap"
                        elif buying_power < qty * price:
                            rec["reason"] = f"insufficient buying power {buying_power} for ~${qty * price}"
                        elif args.dry_run:
                            rec.update({"action": "dry_run_buy", "qty": qty, "limit": str(limit),
                                        "stop": str(stop_px), "target": str(target_px)})
                        else:
                            set_stage(ticker, "reviewing", f"buy {qty} @ {limit}")
                            log_line(f"[{ticker}] broker review: buy {qty} @ {limit}")
                            if stop_px is not None:
                                log_line(f"[{ticker}] bracket: stop {stop_px} / target {target_px}")
                            result = place_signal_order(
                                settings=settings, snapshot=snapshot,
                                order=SignalOrder(symbol=ticker, side="buy",
                                                  quantity=Decimal(qty), limit_price=limit,
                                                  earnings_date=earnings,
                                                  max_order_value=cap,
                                                  take_profit_price=target_px,
                                                  bracket_stop_price=stop_px),
                                env_path=args.env_file)
                            set_stage(ticker, "placing", f"approval {result['approval_id']}")
                            rec.update({"action": "buy_placed", "qty": qty, "limit": str(limit),
                                        "stop": str(stop_px), "target": str(target_px),
                                        "order_id": result["order_id"], "status": result["status"],
                                        "approval_id": result["approval_id"]})
                            log_line(f"[{ticker}] PLACED buy {qty} @ {limit} status={result['status']} order={result['order_id']}")
                    elif sig in SELL_SIGNALS:
                        held = positions.get(ticker, Decimal("0"))
                        if held <= 0:
                            rec["reason"] = f"bearish ({decision}) but no position held"
                        elif args.dry_run:
                            rec.update({"action": "dry_run_sell", "qty": str(held)})
                        else:
                            result = place_signal_order(
                                settings=settings, snapshot=snapshot,
                                order=SignalOrder(symbol=ticker, side="sell",
                                                  quantity=held,
                                                  limit_price=price.quantize(Decimal("0.01"))),
                                env_path=args.env_file)
                            rec.update({"action": "sell_placed", "qty": str(held),
                                    "order_id": result["order_id"], "status": result["status"]})
        except PolicyViolation as exc:
            rec["reason"] = f"gate blocked: {exc}"
        except InvalidOperation as exc:
            rec["reason"] = f"price error: {exc}"
        except Exception as exc:  # noqa: BLE001 - record and continue per ticker
            rec["reason"] = f"error: {type(exc).__name__}: {str(exc)[:300]}"
        final_stage = rec.get("action", "skip")
        set_stage(ticker, final_stage, rec.get("reason", rec.get("order_id", rec.get("decision", ""))))
        log_line(f"[{ticker}] done: {rec.get('action')} {rec.get('reason', '')}")
        results.append(rec)
        print(json.dumps(rec))
        out_dir = Path("outputs/paper/tradingagents") / ticker / trade_date
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "auto.json").write_text(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
