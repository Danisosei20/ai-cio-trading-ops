"""Delta-only GBM odds for long options: the model behind the exit bands.

Pure and seeded - no network, no clock - so the entry gate, the CLI Time
Machine and the tests all run the same math. Model limits are stated once,
here: premium moves delta-only (gamma/vega/theta ignored), which flatters
a LONG option over a multi-day horizon, so treat every absolute number as
an upper bound. The DIRECTION of a policy comparison is the finding.
"""
from __future__ import annotations

import math
import random
import statistics


def annualized_vol(closes: list[float]) -> float | None:
    """Annualized vol from daily log returns (None below 20 bars)."""
    if len(closes) < 20:
        return None
    returns = [math.log(closes[i] / closes[i - 1])
               for i in range(1, len(closes))
               if closes[i - 1] > 0 and closes[i] > 0]
    if len(returns) < 19:
        return None
    return statistics.stdev(returns) * math.sqrt(252)


def simulate_option_odds(*, premium0: float, delta: float, spot0: float,
                         sigma: float, days: int, paths: int,
                         target_pct: float, stop_pct: float,
                         seed: int, arm_pct: float = 0.25,
                         floor_ratio: float = 0.40) -> dict:
    """Simulated exit-band odds. Pure and seeded: same inputs, same answer.

    The exit policy as the guard enforces it (owner 2026-11-16):
      * target_pct <= 0 -> no take-profit; winners run unlimited.
      * the +25% trail arm locks a floor at peak x floor_ratio, and the
        guard exits when premium touches it (TRAIL).
      * -stop_pct always closes (STOP); expiry ends the hold (NEITHER,
        terminal return reported).
    """
    target = premium0 * (1 + target_pct) if target_pct > 0 else None
    stop = premium0 * (1 - stop_pct)
    arm = premium0 * (1 + arm_pct)
    rng = random.Random(seed)
    dt = 1.0 / 252.0
    drift_step = -0.5 * sigma * sigma * dt
    diff_step = sigma * math.sqrt(dt)
    target_first = stop_first = trail_first = neither = 0
    terminal: list[float] = []
    returns: list[float] = []
    for _ in range(paths):
        spot = spot0
        premium = premium0
        peak = premium0
        hit = 0
        exit_ret = 0.0
        for _day in range(max(days, 1)):
            spot *= math.exp(drift_step + diff_step * rng.gauss(0, 1))
            premium = premium0 + delta * (spot - spot0)
            if premium > peak:
                peak = premium
            if target is not None and premium >= target:
                hit = 1
                exit_ret = target_pct
                break
            if premium <= stop:
                hit = -1
                exit_ret = -stop_pct
                break
            if peak >= arm and premium <= peak * floor_ratio:
                hit = 2
                exit_ret = peak * floor_ratio / premium0 - 1
                break
        if hit == 1:
            target_first += 1
        elif hit == -1:
            stop_first += 1
        elif hit == 2:
            trail_first += 1
        else:
            neither += 1
            terminal.append(premium)
            exit_ret = premium / premium0 - 1
        returns.append(exit_ret)
    ev_pct = sum(returns) / paths
    return {
        "paths": paths,
        "target_first": target_first / paths,
        "stop_first": stop_first / paths,
        "trail_first": trail_first / paths,
        "neither": neither / paths,
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "arm_pct": arm_pct,
        "floor_ratio": floor_ratio,
        "ev_pct_on_premium": round(ev_pct, 5),
        "ev_usd_per_contract": round(ev_pct * premium0 * 100, 2),
        "terminal_premium_median": (statistics.median(terminal)
                                    if terminal else None),
    }