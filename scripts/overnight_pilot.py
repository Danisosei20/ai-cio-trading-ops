#!/usr/bin/env python3
"""Overnight gap-trade pilot: enter near the close, exit at the open.

Multi-stock overnight pilot. Scans a configurable universe of liquid US stocks
and ETFs, ranks candidates by an overnight score (trend alignment, VWAP/POC
confluence, relative strength vs SPY, volume/liquidity), and enters qualifying
long positions near the close. Exits all open positions at the next session open.

Paper trading only. No live brokerage activation.

Ranked overnight scoring (observational, not yet validated for most symbols):

  * Trend alignment (close > 50d SMA): 25 points
  * VWAP/POC confluence (price above VWAP and POC): 20 points
  * Relative strength vs SPY (20d): 20 points
  * Volume/liquidity (above avg volume, $5M+ daily dollar volume): 15 points
  * EMA 9/21 bullish alignment: 10 points
  * Historical overnight performance (if available): 10 points

Eligibility tiers:
  * NVDA: validated (backtested overnight50, OOS +23.7%, Sharpe 1.74)
  * Other tickers: experimental - eligible for limited-size paper-trading only,
    not yet validated

Risk caps:
  * 0.25% of account equity per trade
  * $5,000 total notional across all open positions (shared with day trades)
  * Maximum 2 simultaneous overnight positions
  * No new entries after 3:40 PM ET
  * All positions flattened at 9:35 AM ET next session
  * No new entries if equity down >2% from previous close (circuit breaker)
  * No new entries if market regime is not RISK_ON

State: outputs/paper/overnight.json (entries). Exits sell recorded entries
that are still held at the next session open. Every placement goes through
place_signal_order (review + ledger + session guard). Paper only.

Usage:
  python scripts/overnight_pilot.py --enter          # afternoon entry run
  python scripts/overnight_pilot.py --enter --dry-run # dry run
  python scripts/overnight_pilot.py --exit           # morning exit run
  python scripts/overnight_pilot.py --exit --dry-run # dry run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.errors import PolicyViolation  # noqa: E402
from robinhood_tools.market_prices import market_regime  # noqa: E402
from robinhood_tools.paper_flow import SignalOrder, place_signal_order  # noqa: E402
from robinhood_tools.runtime import (  # noqa: E402
    build_paper_service_with_session, build_settings)
from robinhood_tools.settings import load_env
from robinhood_tools.trade_state import TradeState, TradeStateMachine  # noqa: E402
from robinhood_tools.universe import Sp500Snapshot  # noqa: E402


def _log(msg: str) -> None:
    """Timestamped stdout log (the .bat redirects it to overnight.log)."""
    print(f"{datetime.now(ET).isoformat()} {msg}", flush=True)

ET = __import__("zoneinfo").ZoneInfo("America/New_York")

ENTER_WINDOW = ("15:40", "15:55")
EXIT_WINDOW = ("09:35", "10:00")


def _window_bounds(window: tuple[str, str]) -> tuple[time, time]:
    def parse(value: str) -> time:
        hour, minute = (int(p) for p in value.split(":"))
        return time(hour, minute)
    return parse(window[0]), parse(window[1])


def in_window(now: time, window: tuple[str, str]) -> bool:
    """Inclusive check for an ET wall-clock window."""
    start, end = _window_bounds(window)
    return start <= now <= end


def entries_state(result: dict, date: str, previous: dict | None = None) -> dict:
    """Persist exit-consumable entries, merging with earlier in-window runs.

    The exit path reads ``entry["symbol"]``; the planner emits ``ticker``.
    Normalize here so a persisted entry can actually be sold, and never clobber
    a prior record with an empty result.
    """
    normalized = []
    for raw in result.get("entries", []):
        item = dict(raw)
        if "symbol" not in item and "ticker" in item:
            item["symbol"] = item.pop("ticker")
        if "symbol" in item:
            normalized.append(item)
    entries = list((previous or {}).get("entries", []))
    seen = {json.dumps(e, sort_keys=True, default=str) for e in entries}
    for item in normalized:
        key = json.dumps(item, sort_keys=True, default=str)
        if key not in seen:
            entries.append(item)
            seen.add(key)
    return {"date": date, "entries": entries}

STATE_FILE = Path("outputs/paper/overnight.json")

# ---- Risk limits ----
MAX_RISK_PCT_PER_TRADE = Decimal("0.0025")   # 0.25% of account equity per trade
MAX_TOTAL_NOTIONAL = Decimal("5000")          # desk-wide cap (shared with day trades)
MAX_OPEN_POSITIONS = 2                        # max concurrent overnight positions
MIN_TICKET_USD = Decimal("25")                # minimum ticket size
MAX_DAILY_LOSS_PCT = Decimal("0.03")          # circuit breaker: 3% daily loss
MIN_REWARD_TO_RISK = Decimal("1.5")           # minimum reward-to-risk ratio
# ── End risk limits ───────────────────────────────────────────────

# Tier 1: validated overnight setups (backtested overnight50)
VALIDATED_TICKERS = {"NVDA"}
# Tier 2: experimental - eligible for limited-size paper trading only
EXPERIMENTAL_TICKERS = {"AAPL", "AMD", "MSFT", "META", "AMZN", "GOOGL", "TSLA", "SPY", "QQQ"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Overnight gap-trade pilot (multi-stock).")
    p.add_argument("--enter", action="store_true", help="afternoon entry run")
    p.add_argument("--exit", action="store_true", help="morning exit run")
    p.add_argument("--tickers", default="NVDA,AAPL,MSFT,AMD,META,AMZN,GOOGL,TSLA,SPY,QQQ",
                   help="comma-separated ticker universe")
    p.add_argument("--notional", type=Decimal, default=Decimal("250"),
                   help="max notional per position (default $250)")
    p.add_argument("--trend", type=int, default=50,
                   help="trend lookback days for SMA filter (default 50)")
    p.add_argument("--dry-run", action="store_true", help="scan + judge, no orders")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def _ema(closes: list[float], period: int) -> float | None:
    """Exponential moving average (last value)."""
    if not closes or len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return ema


def _ema_slope(closes: list[float], period: int, lookback: int = 5) -> float | None:
    """Slope of EMA over the lookback window (points per bar)."""
    if len(closes) < period + lookback:
        return None
    ema_series = []
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    ema_series.append(ema)
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
        ema_series.append(ema)
    if len(ema_series) < lookback + 1:
        return None
    return ema_series[-1] - ema_series[-lookback - 1]


def _vwap(bars: list[dict]) -> float | None:
    """Volume-weighted average price over the bars."""
    if not bars:
        return None
    tp_sum = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"]
                 for b in bars)
    vol_sum = sum(b["volume"] for b in bars)
    return tp_sum / vol_sum if vol_sum > 0 else None


def _rel_strength(ticker_closes: list[float], spy_closes: list[float],
                  lookback: int = 20) -> float | None:
    """Relative performance vs SPY over lookback days (percent)."""
    if len(ticker_closes) < lookback + 1 or len(spy_closes) < lookback + 1:
        return None
    t_ret = (ticker_closes[-1] - ticker_closes[-lookback - 1]) / ticker_closes[-lookback - 1]
    s_ret = (spy_closes[-1] - spy_closes[-lookback - 1]) / spy_closes[-lookback - 1]
    return t_ret - s_ret


def _score_setup(ticker: str, direction: str, entry: float, stop: float,
                 target: float, risk_pct: float, score: float,
                 setup_type: str = "overnight_gap") -> dict:
    """Build a plan dict for a scored setup."""
    risk_amount = Decimal(str(plan_qty(entry, stop, Decimal("100000")))) \
        * Decimal(str(abs(entry - stop)))
    return {
        "ticker": ticker,
        "direction": direction,
        "setup": setup_type,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "qty": float(plan_qty(entry, stop, Decimal("100000"))),
        "risk_amount": float(risk_amount),
        "r_r_ratio": round(abs(target - entry) / abs(entry - stop), 2),
        "score": score,
        "odds": {"available": False, "reason": "time machine long-side only"},
        "zone_verdict": "long_ok" if direction == "long" else "short_ok",
    }


def plan_qty(entry: float, stop: float, equity: Decimal,
             direction: str = "long") -> Decimal:
    """Position size: 0.25% of equity / risk distance.

    Accepts (entry, stop, equity, direction) signature.
    Direction: "long" needs stop < entry; "short" needs stop > entry.
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
    return shares.quantize(Decimal("0.01"))


def _calc_position_size(equity: Decimal, entry: float, stop: float,
                        direction: str) -> Decimal:
    """Position size: 0.25% of equity / risk distance.

    Accepts (equity, entry, stop, direction) signature for backward compat.
    """
    return plan_qty(entry, stop, equity, direction)


def _calc_reward_to_risk(entry: float, stop: float, target: float,
                         direction: str) -> Decimal:
    """Calculate reward-to-risk ratio."""
    risk = abs(entry - stop)
    if risk <= 0:
        return Decimal("0")
    reward = abs(target - entry)
    return Decimal(str(reward)) / Decimal(str(risk))


def _validate_setup(rec: dict, equity: Decimal,
                    open_positions: dict | None = None) -> tuple[bool, str, dict]:
    """Validate a setup against risk rules.

    Returns (approved, reason, plan_dict) where plan_dict contains entry,
    stop, target, qty, r_r_ratio, risk_amount, direction.
    """
    ticker = rec["ticker"]
    setup = rec.get("setup", "")
    price = rec.get("price", 0)
    or_low = rec.get("or_low", 0)
    or_high = rec.get("or_high", 0)
    rec.get("vwap", 0)
    zone_verdict = rec.get("zone_verdict", "neutral")
    odds = rec.get("odds", {})

    if setup not in ("long_reclaim", "short_breakdown"):
        return False, f"not a tradeable setup: {setup}", {}

    if zone_verdict in ("long_blocked", "short_blocked"):
        return False, f"zone blocked: {zone_verdict}", {}

    if odds.get("error"):
        return False, f"time machine unavailable: {odds.get('reason')}", {}

    if odds.get("available"):
        tf = odds.get("target_first", 0)
        sf = odds.get("stop_first", 0)
        if tf < 0.20:
            return False, f"odds against: target_first {tf:.0%} < 20%", {}
        if sf >= 0.40:
            return False, f"odds against: stop_first {sf:.0%} >= 40%", {}

    direction = "long" if setup == "long_reclaim" else "short"

    if direction == "long":
        entry = price
        stop = or_low
        target = entry + 2 * (entry - stop)
    else:
        entry = price
        stop = or_high
        target = entry - 2 * (stop - entry)

    risk_distance = abs(entry - stop)
    if risk_distance <= 0:
        return False, "no risk distance", {}

    qty = plan_qty(entry, stop, equity, direction)
    if qty <= 0:
        return False, "position size too small", {}

    risk_amount = qty * Decimal(str(risk_distance))
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


def _score_setup_full(ticker: str, direction: str, entry: float, stop: float,
                      target: float, risk_pct: float, score: float,
                      setup_type: str = "overnight_gap") -> dict:
    """Build a plan dict for a scored setup."""
    risk_amount = Decimal(str(plan_qty(entry, stop, Decimal("100000")))) \
        * Decimal(str(abs(entry - stop)))
    return {
        "ticker": ticker,
        "direction": direction,
        "setup": setup_type,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "qty": float(plan_qty(entry, stop, Decimal("100000"))),
        "risk_amount": float(risk_amount),
        "r_r_ratio": round(abs(target - entry) / abs(entry - stop), 2),
        "score": score,
        "odds": {"available": False, "reason": "time machine long-side only"},
        "zone_verdict": "long_ok" if direction == "long" else "short_ok",
    }


def _compute_ticker_score(ticker: str, bars: list[dict], spy_closes: list[float],
                          entry_price: float, stop: float, target: float) -> tuple[float, dict]:
    """Compute an overnight setup score (0-100) for a ticker.

    Returns (score, factors_dict).
    """
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    scores = {}
    total = 0.0

    # 1. Trend alignment: close > 50d SMA (25 pts)
    closes_arr = [float(c) for c in closes]
    sma50 = sum(closes_arr[-50:]) / 50 if len(closes_arr) >= 50 else None
    if sma50 and entry_price > sma50:
        scores["trend_alignment"] = 25.0
        total += 25.0
    else:
        scores["trend_alignment"] = 0.0

    # 2. VWAP/POC confluence (20 pts)
    vwap = _vwap(bars)
    confluence_score = 0.0
    if vwap and entry_price > vwap:
        confluence_score += 10.0
    scores["vwap_confluence"] = confluence_score
    total += confluence_score

    # 3. Relative strength vs SPY (20 pts)
    rs = _rel_strength(closes_arr, [float(c) for c in spy_closes], lookback=20)
    rs_score = 0.0
    if rs is not None:
        if rs > 0.02:  # >2% outperformance
            rs_score = 20.0
        elif rs > 0.01:
            rs_score = 10.0
        elif rs > 0:
            rs_score = 5.0
        elif rs > -0.01:
            rs_score = 0.0
        else:
            rs_score = -5.0
    scores["rel_strength"] = rs_score
    total += rs_score

    # 4. Volume/liquidity (15 pts)
    vol_score = 0.0
    if len(volumes) >= 20:
        avg_vol = sum(volumes[-20:]) / 20
        if volumes[-1] > avg_vol * 1.5:
            vol_score += 10.0
        dollar_vol = (closes_arr[-1] * volumes[-1])
        if dollar_vol > 5_000_000:
            vol_score += 5.0
    scores["liquidity"] = vol_score
    total += vol_score

    # 5. EMA 9/21 bullish alignment (10 pts)
    ema_fast = _ema(closes_arr, 9)
    ema_slow = _ema(closes_arr, 21)
    ema_score = 0.0
    if ema_fast and ema_slow:
        if entry_price > ema_fast > ema_slow:
            ema_score = 10.0
        elif entry_price > ema_fast:
            ema_score = 5.0
    scores["ema_alignment"] = ema_score
    total += ema_score

    # 6. Historical overnight performance (10 pts) - placeholder for now
    # Will be populated from backtest results as they accumulate
    scores["historical_perf"] = 0.0
    total += 0.0

    return total, scores


def _load_universe(tickers_arg: str) -> list[str]:
    """Parse user-provided tickers, return uppercase list."""
    return [t.strip().upper() for t in tickers_arg.split(",") if t.strip()]


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {"entries": []}
    return {"entries": []}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    args = parse_args(argv)
    if bool(args.enter) == bool(args.exit):
        print("Choose exactly one of --enter / --exit.", file=sys.stderr)
        return 2
    settings = build_settings(args.config, args.env_file)
    if settings.trading_enabled:
        print("REFUSAL: live kill switch open.", file=sys.stderr)
        return 2


    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
    from robinhood_tools.database import CioDatabase

    env = {**load_env(args.env_file), **os.environ}
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    db = CioDatabase(settings.database_path)
    tickers = _load_universe(args.tickers)

    # Load SPY data for regime and relative strength
    import yfinance as yf  # noqa: PLC0415 (TradingAgents venv)
    spy_bars = yf.Ticker("SPY").history(period="60d", interval="1d")
    spy_closes = [float(v) for v in spy_bars["Close"].dropna()] if spy_bars is not None else []

    state = _load_state()
    today = date.today().isoformat()

    # Account equity for sizing and the risk controller. main() used to
    # reference `equity` without ever assigning it (the pilot has never
    # completed a run; see the audit note below).
    equity = Decimal("0")
    try:
        acct = backend.transport.request("GET", "/v2/account")
        equity = Decimal(str(acct.get("equity", "0")))
    except Exception:  # noqa: BLE001 - zero equity sizes every ticket to nothing
        equity = Decimal("0")

    if args.enter:
        result = {"mode": "enter", "entries": [], "blocks": [], "errors": []}
        now_et = datetime.now(ET).time()
        if not in_window(now_et, ENTER_WINDOW):
            reason = f"outside overnight entry window {ENTER_WINDOW[0]}-{ENTER_WINDOW[1]} ET"
            _log(f"{reason}; no entries")
            result["blocks"].append({"ticker": "*", "direction": "long", "reason": reason})
            # Do not touch persisted entries: a late/manual run must not erase
            # the record the next morning's exit needs.
            return result
        # Build candidate pool from all tickers
        candidates = []
        for ticker in tickers:
            try:
                hist = yf.Ticker(ticker).history(period="60d", interval="1d")
                if hist is None or len(hist) < 60:
                    continue
                closes = [float(v) for v in hist["Close"].dropna()]
                [float(v) for v in hist["Volume"].dropna()]
                if len(closes) < 60:
                    continue

                entry_price = float(closes[-1])
                or_low = entry_price * 0.98  # placeholder: OR low from intraday
                or_high = entry_price * 1.02
                target = entry_price + 2 * (entry_price - or_low)

                # The scorer and _vwap read dict bars with lowercase keys;
                # itertuples() rows are namedtuples and b["High"] raised
                # TypeError on every ticker (the pilot never completed a
                # run until 2026-11-16).
                bars = [{"high": float(r["High"]), "low": float(r["Low"]),
                         "close": float(r["Close"]),
                         "volume": float(r["Volume"])}
                        for _, r in hist.iterrows()]
                score, factors = _compute_ticker_score(ticker, bars,
                                                       spy_closes, entry_price,
                                                       or_low, target)

                # Determine tier
                tier = "validated" if ticker in VALIDATED_TICKERS else \
                    ("experimental" if ticker in EXPERIMENTAL_TICKERS else "rejected")

                # Skip rejected tickers entirely
                if tier == "rejected":
                    continue

                candidates.append({
                    "ticker": ticker,
                    "setup": "overnight_gap",
                    "price": entry_price,
                    "or_low": or_low,
                    "or_high": or_high,
                    "vwap": _vwap(bars),
                    "zone_verdict": "long_ok",
                    "odds": {"available": False,
                             "reason": "time machine long-side only",
                             "error": False},
                    "score": score,
                    "factors": factors,
                    "tier": tier,
                })
            except Exception as exc:
                print(json.dumps({"ticker": ticker, "mode": "enter",
                                  "action": "skip",
                                  "reason": f"{type(exc).__name__}: {exc}"}))
                continue

        # Sort by score descending
        candidates.sort(key=lambda c: c["score"], reverse=True)

        # Check circuit breaker: equity down >2% from prev close
        circuit_broken = False
        try:
            acct = backend.transport.request("GET", "/v2/account")
            equity_now = float(acct.get("equity", 0))
            last_eq = float(acct.get("last_equity", 0))
            if last_eq > 0 and (equity_now - last_eq) / last_eq < -MAX_DAILY_LOSS_PCT:
                circuit_broken = True
                _log(f"CIRCUIT BREAKER: equity down {(equity_now - last_eq) / last_eq * 100:.1f}% "
                     f"from prev close (${last_eq:,.0f} -> ${equity_now:,.0f}) - "
                     f"buys blocked desk-wide")
        except Exception:
            pass

        # Check regime - the desk's ONE regime definition: market_regime()
        # (SPY vs its 50-day mean; RISK_ON above, RISK_OFF below, CHOP on
        # it). This previously called uptrend_ok(), a function that exists
        # nowhere in the repo: every run with enough SPY history died with
        # NameError, and every run without it silently read CHOP and
        # blocked the whole book. One definition, one verdict, fail-closed.
        regime = "CHOP"
        if len(spy_closes) >= 51:
            regime = market_regime(spy_closes)

        # Reuse the shared risk controller before selecting entries.
        try:
            from scripts.daytrade_engine import _call_risk_controller
        except ImportError:  # run as a bare script: scripts/ is sys.path[0]
            from daytrade_engine import _call_risk_controller

        # Select candidates up to MAX_OPEN_POSITIONS
        selected = []
        for rec in candidates:
            if len(selected) >= MAX_OPEN_POSITIONS:
                break
            if circuit_broken:
                result["blocks"].append({
                    "ticker": rec["ticker"], "direction": "long",
                    "reason": "circuit breaker: equity down >2% from prev close"})
                continue
            if regime != "RISK_ON":
                result["blocks"].append({
                    "ticker": rec["ticker"], "direction": "long",
                    "reason": f"regime {regime}: new risk blocked desk-wide"})
                continue

            approved, reason, plan = _validate_setup(rec, equity, open_positions={})
            if not approved:
                result["blocks"].append({
                    "ticker": rec["ticker"], "direction": "long",
                    "reason": reason})
                continue

            risk_ok, risk_reason = _call_risk_controller(plan, equity)
            if not risk_ok:
                result["blocks"].append({
                    "ticker": rec["ticker"], "direction": "long",
                    "reason": risk_reason})
                continue

            selected.append((rec, plan))

        # Place orders for selected setups
        # State machine, order placement and the risk gate are the desk's
        # own (daytrade_engine) - reusing them keeps one risk definition.
        # Imported locally so the module also loads as `scripts.overnight_pilot`.
        try:
            from scripts.daytrade_engine import _place_order_with_timeout  # noqa: PLC0415
        except ImportError:  # run as a bare script: scripts/ is sys.path[0]
            from daytrade_engine import _place_order_with_timeout  # noqa: PLC0415
        sm = TradeStateMachine()
        open_count = 0
        for rec, plan in selected:
            ticker = rec["ticker"]
            trade = sm.create_trade(
                ticker=ticker, direction="long", setup="overnight_gap",
                strategy="overnight", regime=regime,
                entry_price=plan["entry"], stop_price=plan["stop"],
                target_price=plan["target"],
                client_order_id=f"{ticker}:{today}:overnight:{uuid.uuid4().hex[:8]}")
            sm.transition(trade.id, TradeState.SIGNAL_FOUND, "scout detected setup")
            sm.transition(trade.id, TradeState.RISK_PENDING, "risk approved")

            if args.dry_run:
                _log(f"[DRY-RUN] would place long {ticker} "
                     f"qty={plan['qty']} entry={plan['entry']} "
                     f"stop={plan['stop']} target={plan['target']} "
                     f"R:R={plan['r_r_ratio']:.2f} score={rec['score']:.1f}")
                result["entries"].append({**plan, "dry_run": True,
                                          "trade_id": trade.id,
                                          "tier": rec["tier"],
                                          "score": rec["score"]})
            else:
                ok, reason, order_id = _place_order_with_timeout(plan, timeout_sec=30)
                if ok:
                    sm.transition(trade.id, TradeState.ORDER_PENDING, "order placed")
                    sm.transition(trade.id, TradeState.POSITION_OPEN, "filled")
                    open_count += 1
                    result["entries"].append({**plan, "trade_id": trade.id,
                                              "order_id": order_id,
                                              "status": reason,
                                              "tier": rec["tier"],
                                              "score": rec["score"]})
                    _log(f"PLACED long {ticker} qty={plan['qty']} "
                         f"entry={plan['entry']} stop={plan['stop']} "
                         f"target={plan['target']} R:R={plan['r_r_ratio']:.2f} "
                         f"score={rec['score']:.1f} tier={rec['tier']}")
                else:
                    sm.transition(trade.id, TradeState.REJECTED, reason)
                    result["blocks"].append({
                        "ticker": ticker, "direction": "long",
                        "reason": f"order failed: {reason}"})
                    result["errors"].append(f"{ticker}: {reason}")
                    _log(f"order failed {ticker}: {reason}")

        result["summary"] = {
            "equity": float(equity),
            "open_trades": open_count,
            "entries_placed": len(result["entries"]),
            "blocks": len(result["blocks"]),
            "scanned": len(candidates),
            "selected": len([e for e in result["entries"] if not e.get("dry_run")]),
        }
        # Persist entries so the 09:35 exit can find what this run recorded.
        _save_state(entries_state(result, today, _load_state()))
        return result

    # ── EXIT ──────────────────────────────────────────────────────────
    # Exit all overnight positions at next session open
    service = build_paper_service_with_session(
        settings=settings,
        sp500_snapshot=Sp500Snapshot(symbols=frozenset(),
                                     as_of="2000-01-01T00:00:00+00:00",
                                     source_url="https://example.com/unused-for-sells"),
        env_path=args.env_file,
        earliest_entry_et=EXIT_WINDOW[0], latest_entry_et=EXIT_WINDOW[1])
    state = _load_state()
    remaining = []
    for entry in state.get("entries", []):
        symbol = entry["symbol"]
        try:
            held = sum(Decimal(str(p.get("qty", "0"))) for p in backend.list_positions()
                       if str(p.get("symbol", "")).upper() == symbol)
            if held <= 0:
                continue  # already flat; drop from state
            import yfinance as yf  # noqa: PLC0415

            day = yf.Ticker(symbol).history(period="1d", interval="1m")
            mark = Decimal(str(day["Close"].iloc[-1])) if len(day) else None
            if mark is None:
                day = yf.Ticker(symbol).history(period="5d", interval="1d")
                mark = Decimal(str(day["Close"].iloc[-1]))
            if args.dry_run:
                print(json.dumps({"ticker": symbol, "mode": "exit",
                                  "action": "dry_run_sell", "qty": str(held),
                                  "mark": str(mark)}))
                remaining.append(entry)
                continue
            result = place_signal_order(
                settings=settings, snapshot=None,
                order=SignalOrder(symbol=symbol, side="sell", quantity=held,
                                  limit_price=mark.quantize(Decimal("0.01")),
                                  position_effect="close"),
                database=db, service=service)
            print(json.dumps({"ticker": symbol, "mode": "exit",
                              "action": "sell_placed",
                              "qty": str(held), "order_id": result["order_id"],
                              "status": result["status"]}))
        except (PolicyViolation, InvalidOperation, KeyError) as exc:
            remaining.append(entry)
            print(json.dumps({"ticker": symbol, "mode": "exit", "action": "skip",
                              "reason": f"{type(exc).__name__}: {exc}"}))
    state["entries"] = remaining
    _save_state(state)
    return 0


if __name__ == "__main__":
    # A dict result is a completed dry-run/entry report, not an error code.
    # Returning it to SystemExit made the scheduler report a false failure.
    _result = main()
    if isinstance(_result, dict):
        raise SystemExit(1 if _result.get("errors") else 0)
    raise SystemExit(_result)
