#!/usr/bin/env python3
"""Market regime detector.

Classifies each session as bullish trend, bearish trend, range-bound, or
high-volatility using measurable inputs:

  * ADX (Average Directional Index) — trend strength
  * ATR percentile — volatility regime
  * Market breadth — advancing vs declining issues (S&P 500)
  * VWAP slope — intraday trend direction

Classification rules:

  HIGH_VOL   : ATR percentile >= 80 (regardless of trend)
  BULL_TREND : ADX >= 25 and close > VWAP slope positive and breadth > 0.6
  BEAR_TREND : ADX >= 25 and close < VWAP slope negative and breadth < 0.4
  CHOP       : everything else

All inputs are point-in-time — no look-ahead bias. Validation is left to the
caller (champion/challenger framework) before using regime for strategy
selection.

Usage:
  from robinhood_tools.regime_detector import RegimeDetector
  det = RegimeDetector()
  regime = det.classify(close=..., vwap_slope=..., adx=..., atr_pct=..., breadth=...)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class Regime(str, Enum):
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    CHOP = "CHOP"
    HIGH_VOL = "HIGH_VOL"
    UNKNOWN = "UNKNOWN"


# Thresholds — tunable, validated out-of-sample before live use
ADX_TREND_THRESHOLD = 25.0
ATR_HIGH_VOL_PERCENTILE = 80.0
BREADTH_BULLISH = 0.60
BREADTH_BEARISH = 0.40


@dataclass(frozen=True)
class RegimeInput:
    close: float
    vwap_slope: float  # slope of VWAP over lookback window (points per bar)
    adx: float
    atr_percentile: float  # 0-100
    breadth: float  # advancing / (advancing + declining), 0-1


@dataclass(frozen=True)
class RegimeOutput:
    regime: Regime
    adx: float
    atr_percentile: float
    breadth: float
    vwap_slope: float
    reasons: list[str]


class RegimeDetector:
    """Classifies market regime from point-in-time inputs."""

    def __init__(
        self,
        adx_threshold: float = ADX_TREND_THRESHOLD,
        atr_high_vol_percentile: float = ATR_HIGH_VOL_PERCENTILE,
        breadth_bullish: float = BREADTH_BULLISH,
        breadth_bearish: float = BREADTH_BEARISH,
    ) -> None:
        self.adx_threshold = adx_threshold
        self.atr_high_vol_percentile = atr_high_vol_percentile
        self.breadth_bullish = breadth_bullish
        self.breadth_bearish = breadth_bearish

    def classify(
        self,
        close: float,
        vwap_slope: float,
        adx: float,
        atr_percentile: float,
        breadth: float,
    ) -> RegimeOutput:
        """Classify regime from point-in-time inputs. No look-ahead."""
        reasons: list[str] = []

        # High vol takes precedence — anything can happen in high vol
        if atr_percentile >= self.atr_high_vol_percentile:
            reasons.append(f"ATR percentile {atr_percentile:.1f} >= {self.atr_high_vol_percentile:.0f}")
            return RegimeOutput(
                regime=Regime.HIGH_VOL,
                adx=adx,
                atr_percentile=atr_percentile,
                breadth=breadth,
                vwap_slope=vwap_slope,
                reasons=reasons,
            )

        # Bull trend: ADX strong, price above VWAP slope, breadth constructive
        if (
            adx >= self.adx_threshold
            and vwap_slope > 0
            and breadth >= self.breadth_bullish
        ):
            reasons.append(
                f"ADX {adx:.1f} >= {self.adx_threshold:.0f}, "
                f"VWAP slope {vwap_slope:+.4f} > 0, breadth {breadth:.2f} >= {self.breadth_bullish:.2f}"
            )
            return RegimeOutput(
                regime=Regime.BULL_TREND,
                adx=adx,
                atr_percentile=atr_percentile,
                breadth=breadth,
                vwap_slope=vwap_slope,
                reasons=reasons,
            )

        # Bear trend: ADX strong, price below VWAP slope, breadth weak
        if (
            adx >= self.adx_threshold
            and vwap_slope < 0
            and breadth <= self.breadth_bearish
        ):
            reasons.append(
                f"ADX {adx:.1f} >= {self.adx_threshold:.0f}, "
                f"VWAP slope {vwap_slope:+.4f} < 0, breadth {breadth:.2f} <= {self.breadth_bearish:.2f}"
            )
            return RegimeOutput(
                regime=Regime.BEAR_TREND,
                adx=adx,
                atr_percentile=atr_percentile,
                breadth=breadth,
                vwap_slope=vwap_slope,
                reasons=reasons,
            )

        # Everything else is chop
        reasons.append("no clear trend regime")
        return RegimeOutput(
            regime=Regime.CHOP,
            adx=adx,
            atr_percentile=atr_percentile,
            breadth=breadth,
            vwap_slope=vwap_slope,
            reasons=reasons,
        )


def compute_adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Compute ADX from OHLC data. Returns the latest ADX value or NaN if insufficient data."""
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return float("nan")

    # True Range
    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    # Directional movement
    up_move = [highs[i] - highs[i - 1] for i in range(1, len(highs))]
    down_move = [lows[i - 1] - lows[i] for i in range(1, len(lows))]

    plus_dm = []
    minus_dm = []
    for i in range(len(up_move)):
        up = up_move[i]
        down = down_move[i]
        if up > down and up > 0:
            plus_dm.append(up)
            minus_dm.append(0.0)
        elif down > up and down > 0:
            plus_dm.append(0.0)
            minus_dm.append(down)
        else:
            plus_dm.append(0.0)
            minus_dm.append(0.0)

    # Wilder smoothing
    def wildersmooth(values: list[float], period: int) -> list[float]:
        if len(values) < period:
            return []
        smoothed = [sum(values[:period])]
        for i in range(period, len(values)):
            smoothed.append(smoothed[-1] - (smoothed[-1] / period) + values[i])
        return smoothed

    tr_smooth = wildersmooth(trs, period)
    plus_dm_smooth = wildersmooth(plus_dm, period)
    minus_dm_smooth = wildersmooth(minus_dm, period)

    if len(tr_smooth) < 2 or len(plus_dm_smooth) < 2:
        return float("nan")

    # DI+ and DI-
    plus_di = [100 * p / t for p, t in zip(plus_dm_smooth, tr_smooth)]
    minus_di = [100 * m / t for m, t in zip(minus_dm_smooth, tr_smooth)]

    # DX
    dx_values = []
    for pdi, mdi in zip(plus_di, minus_di):
        denom = pdi + mdi
        if denom > 0:
            dx_values.append(100 * abs(pdi - mdi) / denom)
        else:
            dx_values.append(0.0)

    # ADX = smoothed DX
    if len(dx_values) < period:
        return float("nan")
    adx_values = wildersmooth(dx_values, period)
    if not adx_values:
        return float("nan")
    return adx_values[-1]


def compute_atr_percentile(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14, lookback: int = 100
) -> float:
    """Compute ATR percentile over the lookback window."""
    if len(highs) < period + 1 or len(closes) < period + 1:
        return float("nan")

    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    if len(trs) < period:
        return float("nan")

    atr = sum(trs[-period:]) / period

    # Percentile of current ATR over lookback window
    if len(trs) < lookback:
        window = trs
    else:
        window = trs[-lookback:]

    count_below = sum(1 for t in window if t < atr)
    percentile = (count_below / len(window)) * 100
    return percentile


def compute_vwap_slope(bars: list[dict], lookback: int = 20) -> float:
    """Compute VWAP slope (points per bar) over lookback window.

    bars: list of dicts with 'high', 'low', 'close', 'volume'
    Returns slope of VWAP over the lookback window, or 0 if insufficient data.
    """
    if len(bars) < lookback + 1:
        return 0.0

    def vwap(bars_subset: list[dict]) -> float:
        tp_sum = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in bars_subset)
        vol_sum = sum(b["volume"] for b in bars_subset)
        return tp_sum / vol_sum if vol_sum > 0 else 0.0

    vwap_now = vwap(bars_subset[-lookback:])
    vwap_past = vwap(bars_subset[:lookback])
    return (vwap_now - vwap_past) / lookback


if __name__ == "__main__":
    # Example usage
    det = RegimeDetector()
    result = det.classify(
        close=150.0,
        vwap_slope=0.15,
        adx=28.0,
        atr_percentile=60.0,
        breadth=0.65,
    )
    print(f"Regime: {result.regime.value}")
    print(f"Reasons: {result.reasons}")
