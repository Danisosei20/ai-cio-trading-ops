"""Odds gate for options entries: don't open what the simulation says loses.

Owner decision 2026-11-16. The equity day-trade path has always gated
entries on the Time Machine's odds (>=40% stop-first or <20% target-first
blocks). The options path - where the desk's real money is - had no odds
at all: contracts were opened on zone/RSI/premium filters alone, and the
book lost $10.3k realized over 33 round-trips.

The options Time Machine (scripts/options_time_machine.py) prices the
guard's exit bands for a contract. This module turns that into an entry
gate: simulate the contract under the CURRENT exit policy (no take-profit
by default, -15% stop, +25% arm with a peak x 0.40 floor) and refuse
openers whose simulated expected value on premium is negative.

Fail-closed by design, same as the equity odds gate: no vol estimate
(realized and quoted IV both missing) means no odds, and no odds means no
order. `sigma_for_underlier` is the only network edge and is injectable,
so certification and tests run offline.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

# The guard's policy, kept here so the gate prices what the guard enforces.
DEFAULT_PATHS = 4000
DEFAULT_MIN_EV_PCT = 0.0     # strictly positive EV required
HORIZON_DAYS = 10            # min(DTE, HORIZON_DAYS)


def horizon_for(expiry: str, today: date, cap: int = HORIZON_DAYS) -> int:
    """Trading-day horizon: min(DTE, cap), at least 1."""
    try:
        dte = (date.fromisoformat(str(expiry)) - today).days
    except (TypeError, ValueError):
        return 1
    return max(min(max(dte, 1), cap), 1)


def entry_odds_verdict(*, premium0: float, delta: float, spot0: float,
                       sigma: float, expiry: str, today: date,
                       paths: int = DEFAULT_PATHS,
                       target_pct: float = 0.0, stop_pct: float = 0.15,
                       arm_pct: float = 0.25, floor_ratio: float = 0.40,
                       min_ev_pct: float = DEFAULT_MIN_EV_PCT,
                       seed: int = 7) -> dict:
    """Simulate the contract and return the entry verdict.

    Never raises for a negative result: a losing contract is a refusal
    with its numbers attached, so the log can explain itself.
    """
    from .options_odds import simulate_option_odds

    odds = simulate_option_odds(
        premium0=premium0, delta=delta, spot0=spot0, sigma=sigma,
        days=horizon_for(expiry, today), paths=paths, target_pct=target_pct,
        stop_pct=stop_pct, arm_pct=arm_pct, floor_ratio=floor_ratio,
        seed=seed)
    ev = float(odds["ev_pct_on_premium"])
    verdict = dict(odds)
    verdict["min_ev_pct"] = min_ev_pct
    verdict["approved"] = ev >= min_ev_pct
    verdict["reason"] = (
        f"simulated EV {ev:+.2%} on premium over "
        f"{odds['paths']} paths (stop {odds['stop_first']:.0%} / "
        f"neither {odds['neither']:.0%}) "
        + ("meets" if verdict["approved"] else "below")
        + f" the {min_ev_pct:+.2%} bar")
    return verdict


def sigma_for_underlier(underlying: str, quote_iv: float | None = None,
                        period: str = "60d") -> float | None:
    """Annualized vol: realized from the underlier's daily closes, falling
    back to the contract's quoted IV. None when neither exists."""
    try:
        import yfinance as yf  # noqa: PLC0415

        frame = yf.Ticker(underlying).history(period=period, interval="1d")
        closes = [float(v) for v in frame["Close"].dropna()] if frame is not None else []
    except Exception:  # noqa: BLE001 - no data is an answer, not a crash
        closes = []
    from .options_odds import annualized_vol

    sigma = annualized_vol(closes)
    if sigma is None or sigma <= 0:
        sigma = float(quote_iv) if quote_iv and float(quote_iv) > 0 else None
    return sigma


def require_positive_ev(*, quote, today: date, paths: int = DEFAULT_PATHS,
                        min_ev_pct: float = DEFAULT_MIN_EV_PCT,
                        sigma: float | None = None,
                        sigma_provider=None) -> dict:
    """Entry gate for one long-option opener. Returns the verdict dict.

    Raises PolicyViolation when the contract must not be opened:
    negative simulated EV, or no vol estimate (fail closed).
    """
    from .errors import PolicyViolation

    contract = quote.contract
    premium0 = float(quote.mid())
    delta = float(quote.delta) if quote.delta is not None else 0.5
    iv = float(quote.iv) if quote.iv else None
    vol = sigma if sigma is not None else (
        sigma_provider or sigma_for_underlier)(contract.underlying, iv)
    if vol is None or vol <= 0:
        raise PolicyViolation(
            f"odds gate: no vol estimate for {contract.underlying} "
            f"(realized and quoted IV both missing) - fail closed, no entry")
    verdict = entry_odds_verdict(
        premium0=premium0, delta=delta, spot0=float(quote.underlying_price),
        sigma=float(vol), expiry=contract.expiry, today=today, paths=paths,
        min_ev_pct=min_ev_pct)
    if not verdict["approved"]:
        raise PolicyViolation(
            f"odds gate: {contract.option_symbol} refused - {verdict['reason']}")
    return verdict