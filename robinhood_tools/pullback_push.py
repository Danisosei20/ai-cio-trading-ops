"""Pullback/push trigger engine (applies to every ticker, every day).

Two setups, no chasing:
  PULLBACK CALL: price fades into support (20d SMA zone) and stabilizes
    -> wait for the zone, then enter calls. Invalidation: close below zone.
  BREAKDOWN PUT:  price pushes through support with the zone lost
    -> puts. Invalidation: reclaim of the breakdown level.
  BREAKOUT CALL:  price pushes through the 20d high -> momentum calls.
  WAIT:           price sits between triggers. Doing nothing is the position.

Zones use ATR so volatile and quiet names get proportional levels.
Pure functions; data sourcing lives with callers.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .errors import PolicyViolation

Signal = Literal["CALL_TRIGGER", "CALL_WATCH", "PUT_TRIGGER", "WAIT"]


@dataclass(frozen=True)
class TriggerLevels:
    support: Decimal
    resistance: Decimal
    atr: Decimal
    pullback_low: Decimal   # support - 0.25 ATR
    pullback_high: Decimal  # support + 0.50 ATR
    invalidation: Decimal   # support - 0.25 ATR (close below kills the call)


@dataclass(frozen=True)
class TriggerState:
    signal: Signal
    levels: TriggerLevels
    detail: str = ""


def levels_from_bars(closes: list[float], highs: list[float], lows: list[float]) -> TriggerLevels:
    if len(closes) < 21 or not (len(highs) == len(lows) == len(closes)):
        raise PolicyViolation("Need 21+ aligned bars.")
    sma20 = sum(closes[-20:]) / 20
    res = max(highs[-20:])
    trs = []
    for i in range(max(1, len(closes) - 15), len(closes)):
        hl = highs[i] - lows[i]
        pc = abs(closes[i - 1] - closes[i]) if i > 0 else 0.0
        trs.append(max(hl, pc))
    atr = sum(trs) / len(trs) if trs else 0.0
    if atr <= 0:
        raise PolicyViolation("ATR is not positive.")
    support = Decimal(str(sma20))
    return TriggerLevels(
        support=support,
        resistance=Decimal(str(res)),
        atr=Decimal(str(atr)),
        pullback_low=support - Decimal(str(atr)) * Decimal("0.25"),
        pullback_high=support + Decimal(str(atr)) * Decimal("0.50"),
        invalidation=support - Decimal(str(atr)) * Decimal("0.25"),
    )


def evaluate(close: Decimal, levels: TriggerLevels) -> TriggerState:
    if close > levels.resistance:
        return TriggerState("CALL_TRIGGER", levels, "push through the 20d high: momentum call")
    if close < levels.invalidation:
        return TriggerState("PUT_TRIGGER", levels, "support lost: breakdown put")
    if levels.pullback_low <= close <= levels.pullback_high:
        return TriggerState("CALL_WATCH", levels, "in the pullback zone: await stabilization")
    return TriggerState("WAIT", levels, "between triggers: no position")
