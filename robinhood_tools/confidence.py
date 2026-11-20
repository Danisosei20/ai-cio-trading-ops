#!/usr/bin/env python3
"""Confidence calibration: observational setup scoring for the trading desk.

Computes a setup score (0-100) from multiple confluence factors. The score
is initially OBSERVATIONAL — it does not influence position sizing or
approval. After enough forward-tested trades (>= 30 per strategy), the
score can be promoted to influence sizing via champion/challenger.

Factors:
  - trend_alignment: price vs 50d SMA, EMA 9/21 alignment (0-25)
  - vwap_poc_confluence: price position relative to VWAP, POC, VA (0-20)
  - market_regime: RISK_ON vs CHOP/UNKNOWN (0-20)
  - liquidity: volume, spread, market cap tier (0-15)
  - historical_performance: strategy's win rate and expectancy (0-20)

Each strategy (overnight, intraday, options, swing) is tracked separately
so a successful overnight trade doesn't conceal a failing intraday strategy.

Usage:
  from robinhood_tools.confidence import SetupScorer
  scorer = SetupScorer()
  score = scorer.score(ticker="NVDA", setup="overnight_gap",
                       trend_aligned=True, vwap_above=True, poc_near=True,
                       regime="RISK_ON", liquidity_tier="high",
                       strategy="overnight")
  # score.total = 72, score.factors = {...}
  scorer.record(ticker, score, outcome=None)  # outcome filled later
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")
SCORES_FILE = ROOT / "outputs" / "paper" / "confidence_scores.json"


@dataclass
class SetupScore:
    ticker: str
    setup: str
    strategy: str
    total: int  # 0-100
    factors: dict = field(default_factory=dict)
    confidence_label: str = "low"  # low/medium/high
    scored_at: str = ""
    outcome: str | None = None  # win/loss/breakeven, filled later
    actual_return: float | None = None


@dataclass
class StrategyStats:
    """Per-strategy performance tracking."""
    strategy: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_return: float = 0.0
    avg_score: float = 0.0
    score_sum: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades > 0 else 0.0

    @property
    def expectancy(self) -> float:
        if self.trades == 0:
            return 0.0
        return self.total_return / self.trades


class SetupScorer:
    """Computes and tracks observational setup scores."""

    def __init__(self, scores_path: Path | None = None):
        self.scores_path = scores_path or SCORES_FILE
        self.scores: list[dict] = self._load()
        self.strategy_stats: dict[str, StrategyStats] = self._compute_stats()

    def _load(self) -> list[dict]:
        if self.scores_path.exists():
            try:
                return json.loads(self.scores_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save(self) -> None:
        self.scores_path.parent.mkdir(parents=True, exist_ok=True)
        self.scores_path.write_text(json.dumps(self.scores, indent=2, default=str),
                                    encoding="utf-8")

    def _compute_stats(self) -> dict[str, StrategyStats]:
        stats: dict[str, StrategyStats] = {}
        for s in self.scores:
            strat = s.get("strategy", "unknown")
            if strat not in stats:
                stats[strat] = StrategyStats(strategy=strat)
            st = stats[strat]
            st.trades += 1
            if s.get("outcome") == "win":
                st.wins += 1
            elif s.get("outcome") == "loss":
                st.losses += 1
            if s.get("actual_return") is not None:
                st.total_return += s["actual_return"]
            st.score_sum += s.get("total", 0)
            st.avg_score = st.score_sum / st.trades if st.trades > 0 else 0.0
        return stats

    def score(self, ticker: str, setup: str, strategy: str,
              trend_aligned: bool = False,
              vwap_above: bool = False,
              poc_near: bool = False,
              regime: str = "UNKNOWN",
              liquidity_tier: str = "medium",
              hist_win_rate: float = 0.5) -> SetupScore:
        """Compute a setup score from confluence factors. Observational only."""
        factors = {}
        # Trend alignment (0-25)
        trend_score = 25 if trend_aligned else 0
        factors["trend_alignment"] = trend_score

        # VWAP/POC confluence (0-20)
        confluence_score = 0
        if vwap_above:
            confluence_score += 10
        if poc_near:
            confluence_score += 10
        factors["vwap_poc_confluence"] = confluence_score

        # Market regime (0-20)
        regime_scores = {"RISK_ON": 20, "CHOP": 5, "UNKNOWN": 10}
        regime_score = regime_scores.get(regime, 10)
        factors["market_regime"] = regime_score

        # Liquidity (0-15)
        liq_scores = {"high": 15, "medium": 10, "low": 5}
        liq_score = liq_scores.get(liquidity_tier, 10)
        factors["liquidity"] = liq_score

        # Historical performance (0-20)
        # Only counts after 10+ trades for this strategy
        hist_score = 0
        st = self.strategy_stats.get(strategy)
        if st and st.trades >= 10:
            hist_score = int(min(20, hist_win_rate * 20 + (st.expectancy > 0) * 5))
        factors["historical_performance"] = hist_score

        total = sum(factors.values())
        if total >= 70:
            label = "high"
        elif total >= 40:
            label = "medium"
        else:
            label = "low"

        return SetupScore(
            ticker=ticker, setup=setup, strategy=strategy,
            total=total, factors=factors, confidence_label=label,
            scored_at=datetime.now(ET).isoformat(),
        )

    def record(self, score: SetupScore, outcome: str | None = None,
              actual_return: float | None = None) -> None:
        """Record a scored setup (outcome filled later when known)."""
        d = asdict(score)
        d["outcome"] = outcome
        d["actual_return"] = actual_return
        self.scores.append(d)
        self._save()
        self.strategy_stats = self._compute_stats()

    def update_outcome(self, ticker: str, scored_at: str,
                       outcome: str, actual_return: float) -> None:
        """Fill in the outcome for a previously scored setup."""
        for s in self.scores:
            if s["ticker"] == ticker and s["scored_at"] == scored_at:
                s["outcome"] = outcome
                s["actual_return"] = actual_return
        self._save()
        self.strategy_stats = self._compute_stats()

    def strategy_report(self) -> str:
        """Per-strategy performance report."""
        lines = ["=== Strategy Performance (observational) ==="]
        for name, st in sorted(self.strategy_stats.items()):
            lines.append(
                f"  {name}: {st.trades} trades, {st.wins}W/{st.losses}L, "
                f"win_rate={st.win_rate:.0%}, expectancy={st.expectancy:+.2f}%, "
                f"avg_score={st.avg_score:.0f}")
        # Overall
        total = sum(s.trades for s in self.strategy_stats.values())
        wins = sum(s.wins for s in self.strategy_stats.values())
        lines.append(f"  TOTAL: {total} trades, {wins} wins ({'n/a' if total == 0 else f'{wins/total:.0%}'})")
        return "\n".join(lines)

    def champion_challenger_status(self) -> dict:
        """Check if any strategy has enough data for promotion."""
        result = {}
        for name, st in self.strategy_stats.items():
            promoted = st.trades >= 30 and st.win_rate >= 0.55 and st.expectancy > 0
            result[name] = {
                "trades": st.trades,
                "win_rate": round(st.win_rate, 3),
                "expectancy": round(st.expectancy, 3),
                "promoted": promoted,
                "reason": ("eligible for promotion" if promoted
                           else f"need {max(0, 30 - st.trades)} more trades"
                           if st.trades < 30 else "win_rate or expectancy too low"),
            }
        return result


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Confidence calibration scorer.")
    p.add_argument("--report", action="store_true", help="print strategy report")
    p.add_argument("--status", action="store_true", help="champion/challenger status")
    args = p.parse_args(argv)
    scorer = SetupScorer()
    if args.report:
        print(scorer.strategy_report())
    elif args.status:
        import json as _j
        print(_j.dumps(scorer.champion_challenger_status(), indent=2))
    else:
        print(scorer.strategy_report())
        print()
        print(json.dumps(scorer.champion_challenger_status(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
