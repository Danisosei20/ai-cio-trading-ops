#!/usr/bin/env python3
"""Relative strength / weakness scanner.

Ranks tickers relative to a benchmark (typically SPY) using:

  * Relative performance over multiple lookbacks (1d, 5d, 20d, 60d)
  * Relative volume vs benchmark
  * Sector-relative performance (if sector ETF data available)
  * Price structure vs key moving averages

Outputs a ranked list with RS/WS score (0-100) and supporting metrics.
All inputs are point-in-time — no look-ahead bias.

Usage:
  from robinhood_tools.relative_strength import RelativeStrengthScanner
  scanner = RelativeStrengthScanner()
  ranked = scanner.scan(tickers=["NVDA", "AAPL", "TSLA"], benchmark="SPY")
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelativeStrengthResult:
    ticker: str
    rs_score: float  # 0-100, higher = stronger relative to benchmark
    perf_1d: float
    perf_5d: float
    perf_20d: float
    perf_60d: float
    perf_benchmark_1d: float
    perf_benchmark_5d: float
    perf_benchmark_20d: float
    perf_benchmark_60d: float
    rel_vol_1d: float  # ticker volume / benchmark volume
    above_ma20: bool
    above_ma50: bool
    above_ma200: bool
    classification: str  # "strong", "weak", "neutral"


class RelativeStrengthScanner:
    """Scans tickers for relative strength/weakness vs a benchmark."""

    def __init__(self, benchmark: str = "SPY") -> None:
        self.benchmark = benchmark.upper()

    def scan(
        self,
        tickers: list[str],
        price_data: dict[str, list[dict]],
        benchmark_data: list[dict],
    ) -> list[RelativeStrengthResult]:
        """Scan tickers for relative strength.

        price_data: dict mapping ticker -> list of bar dicts
                    each bar: {"close": float, "volume": float}
        benchmark_data: list of bar dicts for benchmark
        """
        benchmark_perf = self._compute_performances(benchmark_data)
        benchmark_vols = [b["volume"] for b in benchmark_data]

        results: list[RelativeStrengthResult] = []
        for ticker in tickers:
            if ticker not in price_data or not price_data[ticker]:
                continue

            bars = price_data[ticker]
            ticker_perf = self._compute_performances(bars)
            ticker_vols = [b["volume"] for b in bars]

            rs_score = self._compute_score(
                ticker_perf, benchmark_perf,
                ticker_vols, benchmark_vols,
                bars,
            )

            classification = self._classify(rs_score)

            results.append(RelativeStrengthResult(
                ticker=ticker,
                rs_score=rs_score,
                perf_1d=ticker_perf["1d"],
                perf_5d=ticker_perf["5d"],
                perf_20d=ticker_perf["20d"],
                perf_60d=ticker_perf["60d"],
                perf_benchmark_1d=benchmark_perf["1d"],
                perf_benchmark_5d=benchmark_perf["5d"],
                perf_benchmark_20d=benchmark_perf["20d"],
                perf_benchmark_60d=benchmark_perf["60d"],
                rel_vol_1d=self._rel_volume(ticker_vols, benchmark_vols),
                above_ma20=self._above_ma(bars, 20),
                above_ma50=self._above_ma(bars, 50),
                above_ma200=self._above_ma(bars, 200),
                classification=classification,
            ))

        # Sort by RS score descending
        results.sort(key=lambda r: r.rs_score, reverse=True)
        return results

    def _compute_performances(self, bars: list[dict]) -> dict[str, float]:
        """Compute percent change over 1d, 5d, 20d, 60d."""
        if len(bars) < 2:
            return {"1d": 0.0, "5d": 0.0, "20d": 0.0, "60d": 0.0}

        closes = [b["close"] for b in bars]
        current = closes[-1]

        def pct_change(lookback: int) -> float:
            if len(closes) <= lookback:
                return 0.0
            past = closes[-(lookback + 1)]
            if past == 0:
                return 0.0
            return (current - past) / past * 100.0

        return {
            "1d": pct_change(1),
            "5d": pct_change(5),
            "20d": pct_change(20),
            "60d": pct_change(60),
        }

    def _compute_score(
        self,
        ticker_perf: dict[str, float],
        bench_perf: dict[str, float],
        ticker_vols: list[float],
        bench_vols: list[float],
        bars: list[dict],
    ) -> float:
        """Composite RS score 0-100."""
        score = 50.0  # neutral start

        # Relative performance (weighted 50%)
        rel_1d = ticker_perf["1d"] - bench_perf["1d"]
        rel_5d = ticker_perf["5d"] - bench_perf["5d"]
        rel_20d = ticker_perf["20d"] - bench_perf["20d"]
        rel_60d = ticker_perf["60d"] - bench_perf["60d"]

        perf_score = (
            rel_1d * 0.10 +
            rel_5d * 0.20 +
            rel_20d * 0.40 +
            rel_60d * 0.30
        )
        # Scale: 5% relative outperformance = ~25 points
        score += perf_score * 5.0

        # Relative volume (weighted 15%)
        rel_vol = self._rel_volume(ticker_vols, bench_vols)
        vol_score = (rel_vol - 1.0) * 25.0  # 2x volume = +25 points
        score += max(-25, min(25, vol_score))

        # Price vs moving averages (weighted 20%)
        ma_score = 0.0
        if self._above_ma(bars, 20):
            ma_score += 7.0
        if self._above_ma(bars, 50):
            ma_score += 7.0
        if self._above_ma(bars, 200):
            ma_score += 6.0
        score += ma_score

        # Momentum consistency (weighted 15%)
        # All timeframes moving same direction
        pos_count = sum(1 for v in [rel_1d, rel_5d, rel_20d, rel_60d] if v > 0)
        neg_count = sum(1 for v in [rel_1d, rel_5d, rel_20d, rel_60d] if v < 0)
        consistency = (pos_count - neg_count) / 4.0 * 15.0
        score += consistency

        return max(0.0, min(100.0, score))

    def _rel_volume(self, ticker_vols: list[float], bench_vols: list[float]) -> float:
        if not ticker_vols or not bench_vols:
            return 1.0
        ticker_avg = sum(ticker_vols[-20:]) / min(len(ticker_vols), 20)
        bench_avg = sum(bench_vols[-20:]) / min(len(bench_vols), 20)
        if bench_avg == 0:
            return 1.0
        return ticker_avg / bench_avg

    def _above_ma(self, bars: list[dict], period: int) -> bool:
        if len(bars) < period:
            return False
        closes = [b["close"] for b in bars]
        ma = sum(closes[-period:]) / period
        return closes[-1] > ma

    def _classify(self, score: float) -> str:
        if score >= 65:
            return "strong"
        if score <= 35:
            return "weak"
        return "neutral"


if __name__ == "__main__":
    # Example usage
    scanner = RelativeStrengthScanner("SPY")
    print("Relative Strength Scanner ready")
