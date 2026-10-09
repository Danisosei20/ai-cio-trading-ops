"""Deterministic port of the SPY Options V3 scoring core (research use).

Mirrors the indicator's math (EMA/VWAP/RSI/MACD-histogram confirmation,
weighted external leadership, confidence engine, 5-tier thresholds) in
testable Python. Display artifacts (zones, POC rendering, dashboard) and
TradingView data plumbing are intentionally out of scope.
Lookahead discipline: prior-day highs/lows must be passed in already
shifted; this module never looks ahead within the bars it is given.
"""
from __future__ import annotations


def ema(values: list[float], length: int) -> list[float | None]:
    if length < 1:
        raise ValueError("length must be >= 1")
    k = 2 / (length + 1)
    out: list[float | None] = [None] * len(values)
    if len(values) < length:
        return out
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    prev = seed
    for i in range(length, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], length: int = 14) -> float | None:
    if len(values) < length + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        ch = values[i] - values[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_g = sum(gains[:length]) / length
    avg_l = sum(losses[:length]) / length
    for g, loss in zip(gains[length:], losses[length:]):
        avg_g = (avg_g * (length - 1) + g) / length
        avg_l = (avg_l * (length - 1) + loss) / length
    return 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)


def macd_hist(values: list[float], fast: int = 12, slow: int = 26,
              signal: int = 9) -> tuple[float | None, float | None]:
    ef, es = ema(values, fast), ema(values, slow)
    if ef[-1] is None or es[-1] is None:
        return None, None
    line = [a - b if a is not None and b is not None else None for a, b in zip(ef, es)]
    valid = [v for v in line if v is not None]
    sig = ema(valid, signal)
    if not valid or sig[-1] is None:
        return line[-1], None
    return line[-1], sig[-1]


def atr(highs: list[float], lows: list[float], closes: list[float], length: int = 14) -> float | None:
    if len(closes) < length + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(closes[i - 1] - highs[i]),
                       abs(closes[i - 1] - lows[i])))
    return sum(trs[-length:]) / length


def confirmation_score(closes: list[float], highs: list[float], lows: list[float],
                       volumes: list[float], ema9_n: int = 9, ema21_n: int = 21,
                       ema50_n: int = 50, rsi_n: int = 14) -> float:
    """Signed -100..100 from EMA/VWAP/RSI/MACD-histogram. Latest bar only."""
    e9, e21, e50 = ema(closes, ema9_n), ema(closes, ema21_n), ema(closes, ema50_n)
    if e9[-1] is None or e21[-1] is None or e50[-1] is None:
        raise ValueError("insufficient bars")
    vwap = sum((h + lo + c) / 3 * v for h, lo, c, v in
               zip(highs[-20:], lows[-20:], closes[-20:], volumes[-20:])) / max(sum(volumes[-20:]), 1e-9)
    rsi_v = rsi(closes, rsi_n)
    line, sig = macd_hist(closes)
    hist = (line - sig) if line is not None and sig is not None else 0.0
    score = 0.0
    score += 20 if e9[-1] > e21[-1] else (-20 if e9[-1] < e21[-1] else 0)
    score += 20 if closes[-1] > e50[-1] else (-20 if closes[-1] < e50[-1] else 0)
    score += 20 if closes[-1] > vwap else (-20 if closes[-1] < vwap else 0)
    if rsi_v is not None:
        score += 20 if rsi_v >= 55 else (-20 if rsi_v <= 45 else 0)
    score += 20 if hist > 0 else (-20 if hist < 0 else 0)
    return max(-100.0, min(100.0, score))


DEFAULT_WEIGHTS = {"QQQ": 22.0, "DIA": 16.0, "IWM": 16.0, "VIX": 18.0,
                   "XLK": 12.0, "XLF": 8.0, "XLY": 8.0}


def leadership_score(scores: dict[str, float], weights: dict[str, float] | None = None) -> float:
    """Weighted external confirmation. VIX enters inverted (caller negates)."""
    weights = weights or DEFAULT_WEIGHTS
    total = sum(weights.values()) or 1.0
    return max(-100.0, min(100.0, sum(scores.get(k, 0.0) * w for k, w in weights.items()) / total))


def confidence_and_rating(trend: float, momentum: float, institutional: float,
                          leadership: float) -> tuple[float, str]:
    """Overall confidence 0-100 + 5-tier rating at the indicator's thresholds."""
    signed = trend * 0.35 + momentum * 0.20 + institutional * 0.20 + leadership * 0.25
    signed = max(-100.0, min(100.0, signed))
    confidence = max(0.0, min(100.0, 50.0 + signed / 2.0))
    if confidence >= 85:
        rating = "STRONG_CALL"
    elif confidence >= 72:
        rating = "CALL"
    elif confidence <= 15:
        rating = "STRONG_PUT"
    elif confidence <= 28:
        rating = "PUT"
    else:
        rating = "NO_TRADE"
    return confidence, rating
