#!/usr/bin/env python3
"""Autonomous long/short day trading engine.

Scans liquid U.S. stocks and ETFs for bullish and bearish intraday setups,
evaluates risk through the central Portfolio Risk Controller, executes
qualifying trades via Alpaca paper, manages open positions with trailing
stops and profit targets, and flattens every session flat.

Paper trading only. No live orders. No OpenAI integration.

State machine per trade:
  SCANNING → SIGNAL_FOUND → RISK_PENDING → ORDER_PENDING → PARTIALLY_FILLED
  → POSITION_OPEN → EXIT_PENDING → CLOSED

Rejected states: REJECTED, CANCELED, EXPIRED, RECONCILIATION_REQUIRED

Usage:
  python scripts/daytrade_engine.py              # run until flatten time
  python scripts/daytrade_engine.py --dry-run    # scan + judge, no orders
  python scripts/daytrade_engine.py --once       # single cycle then exit
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robinhood_tools.trade_state import TradeStateMachine, TradeState
from robinhood_tools.portfolio_risk import PortfolioRiskController
from robinhood_tools.confidence import SetupScorer
from scripts.daytrade_scout import scout_one
from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport

ET = ZoneInfo("America/New_York")
ROOT_PATH = Path(__file__).resolve().parents[1]

LOG_FILE = ROOT_PATH / "outputs" / "daytrade_engine.log"
STATE_FILE = ROOT_PATH / "outputs" / "daytrade" / f"{datetime.now(ET).date().isoformat()}.json"

# ── Risk limits (configurable) ──────────────────────────────────────────
MAX_RISK_PCT_PER_TRADE = Decimal("0.0025")   # 0.25% of account equity
MIN_REWARD_TO_RISK = Decimal("1.5")          # minimum 1.5:1 planned R:R
MAX_DAY_TRADES = 2                           # max 2 simultaneous day trades
NO_NEW_ENTRIES_AFTER = "15:15"               # no new entries after 3:15 PM ET
FLATTEN_AT = "15:45"                         # flatten by 3:45 PM ET
MAX_TOTAL_NOTIONAL = Decimal("5000")         # desk-wide cap


def _log(msg: str) -> None:
    ts = datetime.now(ET).isoformat()
    line = f"{ts} {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _now_et() -> datetime:
    return datetime.now(ET)


def _parse_time(s: str) -> datetime:
    """Parse 'HH:MM' into today's ET datetime."""
    h, m = s.split(":")
    now = datetime.now(ET)
    return now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def _is_flatten_time(now: datetime | None = None) -> bool:
    """Return True if we should flatten all positions."""
    now = now or _now_et()
    h, m = FLATTEN_AT.split(":")
    flatten_time = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    return now >= flatten_time


def _is_no_new_entries_time(now: datetime | None = None) -> bool:
    """Return True if no new entries should be accepted."""
    now = now or _now_et()
    h, m = NO_NEW_ENTRIES_AFTER.split(":")
    cutoff = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    return now >= cutoff


def _count_open_day_trades() -> int:
    """Count open day trade positions from trade state machine."""
    sm = TradeStateMachine()
    open_trades = sm.get_open_trades()
    return len([t for t in open_trades if t.strategy == "intraday"])


def _load_equity() -> Decimal:
    """Get current account equity from Alpaca paper."""
    try:
        from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
        from robinhood_tools.settings import load_env
        import os as _os
        env = {**load_env(".env"), **_os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        acct = backend.transport.request("GET", "/v2/account")
        return Decimal(str(acct.get("equity", "0")))
    except Exception as exc:
        _log(f"could not fetch equity: {exc}")
        return Decimal("0")


def _calc_position_size(equity: Decimal, entry: float, stop: float,
                        direction: str,
                        remaining_notional: Decimal | None = None) -> Decimal:
    """Calculate position size based on 0.25% risk per trade.

    Risk per trade = equity * 0.25%. Shares = risk / |entry - stop|.
    Returns 0 if risk distance is zero or negative.

    `remaining_notional` is what the order's book can still hold (the short
    book for a short). When given, the size is clamped to
    floor(remaining_notional / entry) — never rounded up, because rounding up
    to one share would breach the very cap being clamped to. If that budget
    cannot hold a single share the size is 0 and the caller must block.
    """
    risk_amount = equity * MAX_RISK_PCT_PER_TRADE
    risk_distance = abs(entry - stop)
    if risk_distance <= 0:
        return Decimal("0")
    # Direction validation: for long, stop must be below entry;
    # for short, stop must be above entry.
    if direction == "long" and stop >= entry:
        return Decimal("0")
    if direction == "short" and stop <= entry:
        return Decimal("0")
    shares = risk_amount / Decimal(str(risk_distance))
    if remaining_notional is not None:
        # A non-positive entry has no valid size, and dividing by it would
        # raise: a zero quote is missing evidence, so fail closed. Long
        # entries never reach this branch (they get no budget).
        if entry <= 0:
            return Decimal("0")
        affordable = (remaining_notional
                      / Decimal(str(entry))).to_integral_value(
                          rounding=ROUND_FLOOR)
        if affordable < 1:
            return Decimal("0")
        shares = min(shares, affordable)
    return shares.quantize(Decimal("0.01"))


def _calc_reward_to_risk(entry: float, stop: float, target: float,
                         direction: str) -> Decimal:
    """Calculate reward-to-risk ratio."""
    risk = abs(entry - stop)
    if risk <= 0:
        return Decimal("0")
    reward = abs(target - entry)
    return Decimal(str(reward)) / Decimal(str(risk))


def _validate_setup(rec: dict, equity: Decimal, open_positions: dict | None = None) -> tuple[bool, str, dict]:
    """Validate a scout setup against risk rules.

    Returns (approved, reason, plan_dict) where plan_dict contains
    entry, stop, target, qty, r_r_ratio, risk_amount, direction.

    Long setups (long_reclaim): buy entry, stop at OR low, target at 2R.
    Short setups (short_breakdown): sell entry, stop at OR high, target at 2R.
      - If we already hold a long position, this is a sell-to-close (exit).
      - If we have no long position, this is a short entry candidate.
    """
    ticker = rec["ticker"]
    setup = rec.get("setup", "")
    price = rec.get("price", 0)
    or_low = rec.get("or_low", 0)
    or_high = rec.get("or_high", 0)
    vwap = rec.get("vwap", 0)
    zone_verdict = rec.get("zone_verdict", "neutral")
    odds = rec.get("odds", {})

    if setup not in ("long_reclaim", "short_breakdown"):
        return False, f"not a tradeable setup: {setup}", {}

    # ── Determine direction & intent ────────────────────────────────
    if setup == "long_reclaim":
        direction = "long"
        is_short = False
    else:  # short_breakdown
        direction = "short"
        # Check if we already hold a long position → sell-to-close, not short entry
        existing_long = open_positions.get(ticker, {}).get("qty", 0) if open_positions else 0
        if existing_long > 0:
            direction = "sell_to_close"
            is_short = False
        else:
            is_short = True

    # ── Technical validation ────────────────────────────────────────
    # Zone gate: fail-closed - only block in the relevant direction
    if setup == "long_reclaim" and zone_verdict == "long_blocked":
        return False, f"zone blocked: {zone_verdict}", {}
    elif setup == "short_breakdown" and zone_verdict == "short_blocked":
        return False, f"zone blocked: {zone_verdict}", {}

    # Time Machine odds: fail-closed on error
    if odds.get("error"):
        return False, f"time machine unavailable: {odds.get('reason')}", {}

    if odds.get("available"):
        tf = odds.get("target_first", 0)
        sf = odds.get("stop_first", 0)
        if tf < 0.20:
            return False, f"odds against: target_first {tf:.0%} < 20%", {}
        if sf >= 0.40:
            return False, f"odds against: stop_first {sf:.0%} >= 40%", {}

    # ── Stop / Target calculation ───────────────────────────────────
    if direction == "long":
        entry = price
        stop = or_low
        target = entry + 2 * (entry - stop)  # 2R default
    elif direction == "short":
        entry = price
        stop = or_high
        target = entry - 2 * (stop - entry)  # 2R default
    else:  # sell_to_close
        # Exit at current price; no stop/target needed for exit
        entry = price
        stop = None
        target = None
        risk_distance = abs(entry - price)  # zero
        qty = open_positions.get(ticker, {}).get("qty", 0) if open_positions else 0
        if qty <= 0:
            return False, "sell_to_close but no long position held", {}
        plan = {
            "ticker": ticker,
            "direction": "sell",
            "setup": setup,
            "entry": round(entry, 2),
            "stop": None,
            "target": None,
            "qty": float(qty),
            "risk_amount": 0.0,
            "r_r_ratio": 0.0,
            "odds": odds,
            "zone_verdict": zone_verdict,
        }
        return True, "approved (sell-to-close)", plan

    risk_distance = abs(entry - stop)
    if risk_distance <= 0:
        return False, "no risk distance", {}

    # Position sizing: 0.25% of equity. A short is sized to the short book's
    # remaining budget, so it dies of risk distance or R:R — never of a cap
    # that is already counting somebody else's longs. `status()` does not
    # reload from disk, so the budget comes from a fresh controller; an
    # unreadable budget fails closed.
    remaining_notional = None
    if direction == "short":
        try:
            exposure = PortfolioRiskController().status()["exposure"]
            remaining_notional = (Decimal(str(exposure["max_per_book"]))
                                  - Decimal(str(exposure["short_notional"])))
        except Exception as exc:  # noqa: BLE001 - missing budget is not permission
            return False, (f"short budget unavailable: {type(exc).__name__}: "
                           f"{exc}"), {}
    qty = _calc_position_size(equity, entry, stop, direction,
                              remaining_notional=remaining_notional)
    if qty <= 0:
        if direction == "short":
            return False, "short notional budget exhausted", {}
        return False, "position size too small", {}

    # Risk amount
    risk_amount = qty * Decimal(str(risk_distance))

    # Reward-to-risk
    r_r = _calc_reward_to_risk(entry, stop, target, direction)
    if r_r < MIN_REWARD_TO_RISK:
        return False, f"R:R {r_r:.2f} < minimum {MIN_REWARD_TO_RISK}", {}

    plan = {
        "ticker": ticker,
        "direction": direction,
        "setup": setup,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "qty": float(qty),
        "risk_amount": float(risk_amount),
        "r_r_ratio": float(r_r),
        "odds": odds,
        "zone_verdict": zone_verdict,
    }
    return True, "approved", plan


def _call_risk_controller(plan: dict, equity: Decimal) -> tuple[bool, str]:
    """Call the central Portfolio Risk Controller (atomic, fail-closed).

    Returns (approved, reason).
    """
    try:
        from robinhood_tools.portfolio_risk import PortfolioRiskController
        ctrl = PortfolioRiskController()
        side = "buy" if plan["direction"] == "long" else "sell"
        # Which book this order touches. A short entry is a `sell` and a
        # sell-to-close is also a `sell`; only the book differs, so the
        # direction is what gets forwarded. Without `short=True` a short is
        # charged to the long book's cap and booked as a *release*, so the
        # short book silently stays empty.
        short = plan["direction"] == "short"
        verdict = ctrl.check(
            symbol=plan["ticker"],
            side=side,
            notional=plan["qty"] * plan["entry"],
            strategy="intraday",
            data_age_sec=0,
            current_equity=float(equity),
            short=short,
        )
        if verdict.approved:
            ctrl.record_order(
                plan["ticker"],
                side,
                "intraday",
                plan["qty"] * plan["entry"],
                short=short,
            )
            return True, "risk approved"
        return False, verdict.reason
    except Exception as exc:  # noqa: BLE001
        return False, f"risk controller error: {type(exc).__name__}: {exc}"


def _place_order(plan: dict) -> tuple[bool, str, str | None]:
    """Place an Alpaca paper order via place_signal_order.

    Returns (approved, reason, order_id).
    """
    try:
        from robinhood_tools.paper_flow import SignalOrder, place_signal_order
        from robinhood_tools.runtime import build_settings, build_paper_service_with_session
        from robinhood_tools.settings import load_env
        from robinhood_tools.database import CioDatabase
        import uuid

        env = load_env(".env")
        settings = build_settings("config/approval_routes.json", ".env")
        snapshot = None  # will be built below

        # Build Alpaca snapshot for eligibility
        from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        acct = backend.transport.request("GET", "/v2/account")

        # Build a simple snapshot object
        class _Snap:
            def __init__(self):
                self.index_etfs = frozenset(settings.index_etf_arbitrage) \
                    if hasattr(settings, "index_etf_arbitrage") else frozenset()
            def require_eligible_purchase(self, ticker):
                pass  # eligibility already checked by risk controller

        snapshot = _Snap()
        service = build_paper_service_with_session(
            settings=settings,
            sp500_snapshot=snapshot,
            env_path=".env",
            earliest_entry_et="10:00",
            latest_entry_et="15:45",
        )

        db = CioDatabase(settings.database_path)
        side = "buy" if plan["direction"] == "long" else "sell"
        candidate_id = f"{plan['ticker']}:{datetime.now(ET).date().isoformat()}:intraday:{uuid.uuid4().hex[:8]}"
        db.record_candidate(candidate_id, plan["ticker"], side,
                            {"source": "daytrade_engine", "setup": plan["setup"],
                             "plan": plan})
        db.record_opinion(candidate_id, "scout", plan["direction"], 60,
                          {"evidence": [f"{plan['setup']} at {plan['entry']}",
                                        f"R:R {plan['r_r_ratio']:.2f}",
                                        f"zone {plan['zone_verdict']}"]})

        order = SignalOrder(
            symbol=plan["ticker"],
            side=side,
            quantity=Decimal(str(plan["qty"])),
            limit_price=Decimal(str(plan["entry"])),
            earnings_date=None,
            max_order_value=Decimal(str(plan["qty"] * plan["entry"])),
            take_profit_price=Decimal(str(plan["target"])) if plan.get("target") else None,
            bracket_stop_price=Decimal(str(plan["stop"])) if plan.get("stop") else None,
        )
        result = place_signal_order(
            settings=settings, snapshot=snapshot,
            order=order, database=db, service=service)
        db.update_candidate_status(candidate_id, "approved")
        return True, result.get("status", "placed"), result.get("order_id")
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        # Provide clearer error for quote/data issues
        if "403" in error_msg or "quote" in error_msg.lower() or "data" in error_msg.lower():
            return False, f"quote/data error: {error_msg}", None
        return False, f"order error: {type(exc).__name__}: {error_msg}", None


def _place_order_with_timeout(plan: dict, timeout_sec: int = 30) -> tuple[bool, str, str | None]:
    """Place an order with a hard wall-clock bound.

    The LLM research call inside place_signal_order can hang indefinitely on
    slow NIM endpoints, so the caller must never block longer than timeout_sec.

    A ThreadPoolExecutor context manager cannot provide that bound: its
    __exit__ calls shutdown(wait=True), which blocks until the worker finishes
    even after future.result(timeout=...) has already raised. The worker
    therefore runs on a daemon thread and the caller returns the moment
    join(timeout_sec) expires.

    A timed-out worker is NOT cancelled - it may still reach the broker after
    we return. That order is unconfirmed, so it is logged as needing
    reconciliation rather than being silently assumed absent.
    """
    import threading

    box: dict = {}

    def _run() -> None:
        try:
            box["result"] = _place_order(plan)
        except BaseException as exc:  # noqa: BLE001 - surfaced to caller
            box["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=_run, name="place-order", daemon=True)
    worker.start()
    worker.join(timeout_sec)

    if worker.is_alive():
        ticker = plan.get("ticker", "?")
        _log(f"ORDER TIMEOUT {ticker} after {timeout_sec}s - "
             f"unconfirmed, reconcile against broker before next entry")
        return False, f"research timeout after {timeout_sec}s (order unconfirmed)", None
    if "error" in box:
        return False, f"order error: {box['error']}", None
    return box.get("result", (False, "order error: no result", None))


def run_cycle(dry_run: bool = False, tickers: str | None = None) -> dict:
    """Run one intraday trading cycle.

    1. Scout setups (long + short)
    2. Validate each setup (technical + odds + risk)
    3. Call central risk controller (atomic)
    4. Place orders (paper)
    5. Update trade state machine

    Returns a dict of cycle results.
    """
    sm = TradeStateMachine()
    ctrl = PortfolioRiskController()
    equity = _load_equity()
    today = _now_et().date().isoformat()

    result = {"date": today, "cycle": datetime.now(ET).isoformat(),
              "setups": [], "entries": [], "blocks": []}

    # ── Load open positions from state machine ────────────────────────
    open_trades = sm.get_open_trades()
    open_positions = {}
    for t in open_trades:
        open_positions[t.ticker] = {
            "qty": float(t.filled_qty or t.qty),
            "direction": t.direction,
            "trade_id": t.id,
        }
    open_count = len([t for t in open_trades if t.direction in ("long", "short")])

    # ── Scan ──────────────────────────────────────────────────────────
    try:
        from scripts.daytrade_scout import main as scout_main
        import scripts.daytrade_scout as scout_mod
        if tickers:
            scout_mod.WATCHLIST = tuple(t.strip().upper()
                                        for t in tickers.split(",") if t.strip())
        scout_main([])
        scout_file = ROOT_PATH / "outputs" / "daytrade" / f"{today}.json"
        if scout_file.exists():
            scout = json.loads(scout_file.read_text(encoding="utf-8"))
        else:
            scout = {"setups": []}
    except Exception as exc:
        _log(f"scout error: {type(exc).__name__}: {exc}")
        scout = {"setups": []}

    # ── Evaluate each setup ──────────────────────────────────────────
    for rec in scout.get("setups", []):
        setup = rec.get("setup", "")
        if setup not in ("long_reclaim", "short_breakdown"):
            continue

        ticker = rec["ticker"]
        raw_direction = "long" if setup == "long_reclaim" else "short"

        # ── Determine actual action (sell-to-close vs new entry) ─────
        existing_pos = open_positions.get(ticker, {})
        existing_qty = existing_pos.get("qty", 0)

        if setup == "short_breakdown" and existing_qty > 0:
            # Sell-to-close: we hold a long and got a bearish signal
            direction = "sell_to_close"
        elif setup == "short_breakdown":
            direction = "short"  # New short entry
        else:
            direction = "long"

        # Dedup: don't re-trade same ticker+setup today
        existing = [t for t in sm.get_open_trades()
                    if t.ticker == ticker and t.setup == setup]
        if existing:
            result["blocks"].append({
                "ticker": ticker, "direction": direction,
                "reason": "already have active setup for this ticker"})
            continue

        # Check max concurrent trades (long/short entries only, not exits)
        if direction in ("long", "short") and open_count >= MAX_DAY_TRADES:
            result["blocks"].append({
                "ticker": ticker, "direction": direction,
                "reason": f"max {MAX_DAY_TRADES} concurrent day trades"})
            continue

        # No new entries after cutoff
        if _is_no_new_entries_time() and direction in ("long", "short"):
            result["blocks"].append({
                "ticker": ticker, "direction": direction,
                "reason": "no new entries after 15:15 ET"})
            continue

        # Validate setup (fail-closed, pass open positions for sell-to-close)
        approved, reason, plan = _validate_setup(rec, equity, open_positions)
        if not approved:
            result["blocks"].append({
                "ticker": ticker, "direction": direction,
                "reason": reason})
            _log(f"blocked {ticker} {direction}: {reason}")
            continue

        # ── Sell-to-close: place sell order, no risk gate needed ─────
        if plan["direction"] == "sell":
            _log(f"sell-to-close {ticker} qty={plan['qty']} @ {plan['entry']}")
            if not dry_run:
                sell_plan = {**plan, "direction": "sell"}
                ok, reason, order_id = _place_order(sell_plan)
                if ok:
                    # Close the open trade record
                    existing_trade = [t for t in sm.get_open_trades()
                                      if t.ticker == ticker][0]
                    sm.transition(existing_trade.id, TradeState.ORDER_PENDING,
                                  "sell order placed")
                    sm.transition(existing_trade.id, TradeState.POSITION_OPEN,
                                  "filled")
                    open_count -= 1
                    result["entries"].append({**plan, "trade_id": existing_trade.id,
                                              "order_id": order_id,
                                              "status": reason,
                                              "action": "sell_to_close"})
                    _log(f"SOLD {ticker} qty={plan['qty']} @ {plan['entry']}")
                else:
                    result["blocks"].append({
                        "ticker": ticker, "direction": direction,
                        "reason": f"sell order failed: {reason}"})
                    _log(f"sell order failed {ticker}: {reason}")
            continue

        # ── Risk controller (atomic, fail-closed) for new entries ───
        risk_ok, risk_reason = _call_risk_controller(plan, equity)
        if not risk_ok:
            result["blocks"].append({
                "ticker": ticker, "direction": direction,
                "reason": risk_reason})
            _log(f"risk blocked {ticker} {direction}: {risk_reason}")
            continue

        # Create trade record
        trade = sm.create_trade(
            ticker=ticker, direction=direction, setup=setup,
            strategy="intraday", regime=rec.get("zone_verdict", ""),
            entry_price=plan["entry"], stop_price=plan["stop"],
            target_price=plan["target"],
            client_order_id=f"{ticker}:{today}:{setup}:{int(_now_et().timestamp())}")
        sm.transition(trade.id, TradeState.SIGNAL_FOUND, "scout detected setup")
        sm.transition(trade.id, TradeState.RISK_PENDING, "risk approved")

        if dry_run:
            _log(f"[DRY-RUN] would place {direction} {ticker} "
                 f"qty={plan['qty']} entry={plan['entry']} "
                 f"stop={plan['stop']} target={plan['target']} "
                 f"R:R={plan['r_r_ratio']:.2f}")
            result["entries"].append({**plan, "dry_run": True,
                                      "trade_id": trade.id})
        else:
            # Place order with timeout to prevent hanging on slow LLM research
            ok, reason, order_id = _place_order_with_timeout(plan, timeout_sec=30)
            if ok:
                sm.transition(trade.id, TradeState.ORDER_PENDING, "order placed")
                sm.transition(trade.id, TradeState.POSITION_OPEN, "filled")
                open_count += 1
                result["entries"].append({**plan, "trade_id": trade.id,
                                          "order_id": order_id,
                                          "status": reason})
                _log(f"PLACED {direction} {ticker} qty={plan['qty']} "
                     f"entry={plan['entry']} stop={plan['stop']} "
                     f"target={plan['target']} R:R={plan['r_r_ratio']:.2f}")
            else:
                sm.transition(trade.id, TradeState.REJECTED, reason)
                result["blocks"].append({
                    "ticker": ticker, "direction": direction,
                    "reason": f"order failed: {reason}"})
                _log(f"order failed {ticker}: {reason}")

    result["summary"] = {
        "equity": float(equity),
        "open_trades": open_count,
        "entries_placed": len(result["entries"]),
        "blocks": len(result["blocks"]),
    }
    return result


def manage_exits(dry_run: bool = False) -> dict:
    """Manage open positions: trailing stops, profit targets, time exits.

    Checks each open position:
    - If price hit target → exit
    - If price hit stop → exit
    - If trailing stop armed and price dropped below floor → exit
    - If time-based exit (after profit threshold met + time elapsed) → exit
    - If flatten time → exit all
    """
    sm = TradeStateMachine()
    ctrl = PortfolioRiskController()
    result = {"exits": [], "blocks": []}

    open_trades = sm.get_open_trades()
    flatten = _is_flatten_time()

    for trade in open_trades:
        ticker = trade.ticker
        direction = trade.direction
        entry = trade.entry_price
        stop = trade.stop_price
        target = trade.target_price

        # Get current price (best effort from yfinance)
        try:
            import yfinance as yf
            frame = yf.Ticker(ticker).history(period="1d", interval="5m")
            if frame is None or frame.empty:
                continue
            current = float(frame["Close"].iloc[-1])
        except Exception as exc:
            _log(f"could not get price for {ticker}: {exc}")
            continue

        should_exit = False
        exit_reason = ""

        if flatten:
            should_exit = True
            exit_reason = "end of day flatten"
        elif direction == "long":
            if current <= stop:
                should_exit = True
                exit_reason = "stop loss hit"
            elif current >= target:
                should_exit = True
                exit_reason = "profit target hit"
        else:  # short
            if current >= stop:
                should_exit = True
                exit_reason = "stop loss hit"
            elif current <= target:
                should_exit = True
                exit_reason = "profit target hit"

        if not should_exit:
            continue

        if dry_run:
            _log(f"[DRY-RUN] would exit {direction} {ticker} at {current:.2f} "
                 f"({exit_reason})")
            result["exits"].append({**plan_from_trade(trade), "dry_run": True,
                                    "exit_reason": exit_reason,
                                    "exit_price": current})
        else:
            ok, reason, order_id = _exit_position(trade, current, exit_reason)
            if ok:
                sm.mark_closed(trade.id, exit_reason,
                              (current - entry) if direction == "long"
                              else (entry - current))
                result["exits"].append({**plan_from_trade(trade),
                                        "exit_reason": exit_reason,
                                        "exit_price": current,
                                        "order_id": order_id})
                _log(f"EXIT {direction} {ticker} at {current:.2f} "
                     f"({exit_reason})")
            else:
                result["blocks"].append({
                    "ticker": ticker, "direction": direction,
                    "reason": f"exit failed: {reason}"})
                sm.transition(trade.id, TradeState.RECONCILIATION_REQUIRED,
                              f"exit failed: {reason}")

    result["summary"] = {"exits": len(result["exits"]),
                         "blocks": len(result["blocks"])}
    return result


def _exit_position(trade, current_price: float, reason: str) -> tuple[bool, str, str | None]:
    """Exit a position via Alpaca paper order.

    Returns (approved, reason, order_id).
    """
    try:
        from robinhood_tools.paper_flow import SignalOrder, place_signal_order
        from robinhood_tools.runtime import build_settings, build_paper_service_with_session
        from robinhood_tools.settings import load_env
        from robinhood_tools.database import CioDatabase
        import uuid

        env = load_env(".env")
        settings = build_settings("config/approval_routes.json", ".env")

        class _Snap:
            def __init__(self):
                self.index_etfs = frozenset(settings.index_etf_arbitrage) \
                    if hasattr(settings, "index_etf_arbitrage") else frozenset()
            def require_eligible_purchase(self, ticker):
                pass

        snapshot = _Snap()
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        service = build_paper_service_with_session(
            settings=settings, sp500_snapshot=snapshot,
            env_path=".env",
            earliest_entry_et="09:45",
            latest_entry_et="15:45",
        )

        db = CioDatabase(settings.database_path)
        side = "sell" if trade.direction == "long" else "buy"
        candidate_id = f"{trade.ticker}:{datetime.now(ET).date().isoformat()}:exit:{uuid.uuid4().hex[:8]}"
        db.record_candidate(candidate_id, trade.ticker, side,
                            {"source": "daytrade_engine_exit", "setup": trade.setup,
                             "reason": reason})

        qty = Decimal(str(trade.filled_qty or trade.qty))
        order = SignalOrder(
            symbol=trade.ticker,
            side=side,
            quantity=qty,
            limit_price=Decimal(str(current_price)),
            earnings_date=None,
            max_order_value=qty * Decimal(str(current_price)),
            take_profit_price=None,
            bracket_stop_price=None,
        )
        result = place_signal_order(
            settings=settings, snapshot=snapshot,
            order=order, database=db, service=service)
        db.update_candidate_status(candidate_id, "approved")
        return True, result.get("status", "exited"), result.get("order_id")
    except Exception as exc:  # noqa: BLE001
        return False, f"exit error: {type(exc).__name__}: {exc}", None


def plan_from_trade(trade) -> dict:
    """Convert a TradeRecord to a plan dict."""
    return {
        "ticker": trade.ticker,
        "direction": trade.direction,
        "setup": trade.setup,
        "entry": trade.entry_price,
        "stop": trade.stop_price,
        "target": trade.target_price,
        "qty": trade.filled_qty or trade.qty,
        "trade_id": trade.id,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Autonomous long/short day trading engine.")
    ap.add_argument("--interval", type=int, default=5,
                    help="minutes between scan cycles (default 5)")
    ap.add_argument("--start", default="09:45",
                    help="start time ET HH:MM (default 09:45)")
    ap.add_argument("--end", default="15:45",
                    help="flatten time ET HH:MM (default 15:45)")
    ap.add_argument("--tickers", default=None,
                    help="override watchlist (comma list)")
    ap.add_argument("--dry-run", action="store_true",
                    help="scan + judge, no orders")
    ap.add_argument("--once", action="store_true",
                    help="single cycle then exit")
    ap.add_argument("--manage-only", action="store_true",
                    help="only manage exits, no new entries")
    args = ap.parse_args(argv)

    start_dt = _parse_time(args.start)
    end_dt = _parse_time(args.end)

    _log(f"day trade engine starting: {args.start}-{args.end} ET, "
         f"interval={args.interval}min, dry_run={args.dry_run}")

    now = _now_et()
    if now < start_dt and not args.once:
        wait = (start_dt - now).total_seconds()
        _log(f"waiting {wait/60:.0f} min until {args.start} ET")
        time.sleep(wait)

    cycle = 0
    while True:
        now = _now_et()
        if now > end_dt:
            _log("end of day reached, flattening all positions")
            run_flatten()
            break

        # Manage exits first (highest priority)
        exit_result = manage_exits(dry_run=args.dry_run)
        if exit_result["exits"]:
            _log(f"exits: {exit_result['summary']['exits']} placed, "
                 f"{exit_result['summary']['blocks']} blocked")

        # Scan for new entries (unless flatten time or manage-only)
        if not args.manage_only and not _is_flatten_time():
            cycle_result = run_cycle(dry_run=args.dry_run, tickers=args.tickers)
            if cycle_result["entries"]:
                _log(f"entries: {cycle_result['summary']['entries_placed']} placed, "
                     f"{cycle_result['summary']['blocks']} blocked")
            cycle += 1

        if args.once:
            break

        # Wait for next cycle
        now = _now_et()
        next_cycle = now + timedelta(minutes=args.interval)
        if next_cycle > end_dt:
            break
        sleep_sec = (next_cycle - now).total_seconds()
        _log(f"sleeping {sleep_sec/60:.0f} min until next cycle")
        time.sleep(sleep_sec)

    # Final flatten
    if not args.dry_run and not args.once:
        _log("final flatten at end of day")
        run_flatten()

    _log("day trade engine done")
    return 0


def run_flatten() -> None:
    """Trigger the 15:45 flatten via flatten_daytrades.py."""
    try:
        from scripts.flatten_daytrades import main as flatten_main
        flatten_main([])
    except Exception as exc:
        _log(f"flatten error: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())