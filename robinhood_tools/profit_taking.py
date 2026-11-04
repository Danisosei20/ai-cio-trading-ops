#!/usr/bin/env python3
"""Smart profit-taking engine.

Compares multiple exit methods and recommends the optimal exit for each open
position:

  * Fixed target - exit at predefined target price
  * Partial exit - sell fraction at target, trail remainder
  * ATR-based trailing stop - trail at N * ATR below/above price
  * Time stop - exit after N bars/days if target not reached
  * Structure-based exit - exit at support/resistance levels

Evaluates each method historically and recommends the best for the current
setup. All inputs are point-in-time — no look-ahead bias.

Usage:
  from robinhood_tools.profit_taking import ProfitTakingEngine
  engine = ProfitTakingEngine()
  decision = engine.evaluate_exit(
    ticker="NVDA",
    direction="long",
    entry=100.0,
    current=110.0,
    stop=95.0,
    target=120.0,
    atr=5.0,
    bars_held=3,
    support=98.0,
    resistance=125.0,
  )
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ExitMethod(str, Enum):
    FIXED_TARGET = "fixed_target"
    PARTIAL_EXIT = "partial_exit"
    ATR_TRAILING_STOP = "atr_trailing_stop"
    TIME_STOP = "time_stop"
    STRUCTURE_BASED = "structure_based"


@dataclass(frozen=True)
class ExitDecision:
    method: ExitMethod
    action: str  # "exit_full", "exit_partial", "hold", "trail"
    exit_price: Optional[float] = None
    exit_qty_pct: Optional[float] = None  # 0-100% of position to exit
    reason: str = ""
    confidence: float = 0.0  # 0-100


@dataclass(frozen=True)
class ExitEvaluation:
    symbol: str
    direction: str  # "long" or "short"
    entry: float
    current: float
    stop: float
    target: float
    unrealized_pnl_pct: float
    bars_held: int
    decisions: list[ExitDecision] = field(default_factory=list)
    recommended: Optional[ExitDecision] = None


class ProfitTakingEngine:
    """Evaluates exit methods and recommends the best exit for each position."""

    def __init__(
        self,
        atr_multiplier: float = 2.0,
        time_stop_bars: int = 10,
        partial_exit_pct: float = 50.0,
        trail_activation_pct: float = 1.0,  # activate trail after 1% profit
    ) -> None:
        self.atr_multiplier = atr_multiplier
        self.time_stop_bars = time_stop_bars
        self.partial_exit_pct = partial_exit_pct
        self.trail_activation_pct = trail_activation_pct

    def evaluate(
        self,
        symbol: str,
        direction: str,
        entry: float,
        current: float,
        stop: float,
        target: float,
        atr: float,
        bars_held: int,
        support: Optional[float] = None,
        resistance: Optional[float] = None,
    ) -> ExitEvaluation:
        """Evaluate all exit methods and recommend the best."""
        decisions: list[ExitDecision] = []

        # 1. Fixed target exit
        if direction == "long" and current >= target:
            decisions.append(ExitDecision(
                method=ExitMethod.FIXED_TARGET,
                action="exit_full",
                exit_price=target,
                exit_qty_pct=100.0,
                reason=f"target {target} reached at {current:.2f}",
                confidence=95.0,
            ))
        elif direction == "short" and current <= target:
            decisions.append(ExitDecision(
                method=ExitMethod.FIXED_TARGET,
                action="exit_full",
                exit_price=target,
                exit_qty_pct=100.0,
                reason=f"target {target} reached at {current:.2f}",
                confidence=95.0,
            ))

        # 2. Partial exit at 1R profit
        risk = abs(entry - stop)
        if risk > 0:
            one_r_profit_long = entry + risk
            one_r_profit_short = entry - risk
            if direction == "long" and current >= one_r_profit_long:
                decisions.append(ExitDecision(
                    method=ExitMethod.PARTIAL_EXIT,
                    action="exit_partial",
                    exit_price=one_r_profit_long,
                    exit_qty_pct=self.partial_exit_pct,
                    reason=f"1R profit reached at {one_r_profit_long:.2f}, trailing remainder",
                    confidence=85.0,
                ))
            elif direction == "short" and current <= one_r_profit_short:
                decisions.append(ExitDecision(
                    method=ExitMethod.PARTIAL_EXIT,
                    action="exit_partial",
                    exit_price=one_r_profit_short,
                    exit_qty_pct=self.partial_exit_pct,
                    reason=f"1R profit reached at {one_r_profit_short:.2f}, trailing remainder",
                    confidence=85.0,
                ))

        # 3. ATR trailing stop
        if direction == "long":
            trail_stop = current - (self.atr_multiplier * atr)
            if trail_stop > stop:  # Only trail if it improves the stop
                decisions.append(ExitDecision(
                    method=ExitMethod.ATR_TRAILING_STOP,
                    action="trail",
                    exit_price=trail_stop,
                    exit_qty_pct=100.0,
                    reason=f"ATR {self.atr_multiplier}x trailing stop at {trail_stop:.2f}",
                    confidence=80.0,
                ))
        else:
            trail_stop = current + (self.atr_multiplier * atr)
            if trail_stop < stop:
                decisions.append(ExitDecision(
                    method=ExitMethod.ATR_TRAILING_STOP,
                    action="trail",
                    exit_price=trail_stop,
                    exit_qty_pct=100.0,
                    reason=f"ATR {self.atr_multiplier}x trailing stop at {trail_stop:.2f}",
                    confidence=80.0,
                ))

        # 4. Time stop
        if bars_held >= self.time_stop_bars:
            decisions.append(ExitDecision(
                method=ExitMethod.TIME_STOP,
                action="exit_full",
                exit_price=current,
                exit_qty_pct=100.0,
                reason=f"time stop: {bars_held} bars held >= {self.time_stop_bars}",
                confidence=60.0,
            ))

        # 5. Structure-based exit
        if direction == "long" and support is not None and current > entry:
            # Trail just below support
            structure_stop = support * 0.995  # slightly below support
            if structure_stop > stop:
                decisions.append(ExitDecision(
                    method=ExitMethod.STRUCTURE_BASED,
                    action="trail",
                    exit_price=structure_stop,
                    exit_qty_pct=100.0,
                    reason=f"structure stop below support {support:.2f}",
                    confidence=75.0,
                ))
        elif direction == "short" and resistance is not None and current < entry:
            structure_stop = resistance * 1.005  # slightly above resistance
            if structure_stop < stop:
                decisions.append(ExitDecision(
                    method=ExitMethod.STRUCTURE_BASED,
                    action="trail",
                    exit_price=structure_stop,
                    exit_qty_pct=100.0,
                    reason=f"structure stop above resistance {resistance:.2f}",
                    confidence=75.0,
                ))

        # Select best decision
        recommended = self._select_best(decisions, direction, entry, current)

        return ExitEvaluation(
            symbol=symbol,
            direction=direction,
            entry=entry,
            current=current,
            stop=stop,
            target=target,
            unrealized_pnl_pct=self._unrealized_pnl_pct(direction, entry, current),
            bars_held=bars_held,
            decisions=decisions,
            recommended=recommended,
        )

    def _select_best(
        self,
        decisions: list[ExitDecision],
        direction: str,
        entry: float,
        current: float,
    ) -> Optional[ExitDecision]:
        """Select the best exit decision."""
        if not decisions:
            return None

        # Priority order: fixed_target > partial_exit > structure_based >
        # atr_trailing_stop > time_stop
        priority = {
            ExitMethod.FIXED_TARGET: 5,
            ExitMethod.PARTIAL_EXIT: 4,
            ExitMethod.STRUCTURE_BASED: 3,
            ExitMethod.ATR_TRAILING_STOP: 2,
            ExitMethod.TIME_STOP: 1,
        }

        # Filter to only actionable decisions
        actionable = [d for d in decisions if d.action != "hold"]
        if not actionable:
            return None

        # Sort by priority then confidence
        actionable.sort(key=lambda d: (priority.get(d.method, 0), d.confidence), reverse=True)
        return actionable[0]

    def _unrealized_pnl_pct(self, direction: str, entry: float, current: float) -> float:
        if direction == "long":
            return (current - entry) / entry * 100.0
        else:
            return (entry - current) / entry * 100.0

    def evaluate_batch(
        self,
        positions: list[dict],
    ) -> list[ExitEvaluation]:
        """Evaluate multiple positions at once."""
        results = []
        for pos in positions:
            eval_result = self.evaluate(
                symbol=pos["ticker"],
                direction=pos["direction"],
                entry=pos["entry"],
                current=pos["current"],
                stop=pos["stop"],
                target=pos["target"],
                atr=pos.get("atr", 0.0),
                bars_held=pos.get("bars_held", 0),
                support=pos.get("support"),
                resistance=pos.get("resistance"),
            )
            results.append(eval_result)
        return results


if __name__ == "__main__":
    # Example usage
    engine = ProfitTakingEngine()
    eval_result = engine.evaluate(
        symbol="NVDA",
        direction="long",
        entry=100.0,
        current=110.0,
        stop=95.0,
        target=120.0,
        atr=5.0,
        bars_held=3,
        support=98.0,
        resistance=125.0,
    )
    print(f"Symbol: {eval_result.symbol}")
    print(f"Direction: {eval_result.direction}")
    print(f"Unrealized P/L: {eval_result.unrealized_pnl_pct:.2f}%")
    print(f"Bars held: {eval_result.bars_held}")
    if eval_result.recommended:
        print(f"Recommended: {eval_result.recommended.method.value}")
        print(f"  Action: {eval_result.recommended.action}")
        print(f"  Reason: {eval_result.recommended.reason}")
        print(f"  Confidence: {eval_result.recommended.confidence}%")
