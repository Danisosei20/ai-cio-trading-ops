"""Multi-agent desk pipeline, Phase 3 PR 1 (analysis only, no orders).

Typed messages flow market/news/macro/scout -> bull/bear/red debate ->
judge -> deterministic risk. Every step is persisted to the candidate
ledger (`trade_candidates`, `agent_opinions`, `option_candidates`,
`risk_decisions`) so the UI can render the full thought process.

No LLM calls here: analysts are caller-supplied (fixtures, rules, or a
later LLM-backed PR); the judge is deterministic. Execution policy is
OBSERVE — this module cannot place orders by construction (no broker,
no credentials, no network).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from .database import CioDatabase
from .errors import PolicyViolation

ET = ZoneInfo("America/New_York")
Verdict = Literal["STRONG_CALL", "CALL", "NO_TRADE", "PUT", "STRONG_PUT"]


@dataclass(frozen=True)
class MarketView:
    bias: Literal["bullish", "bearish", "neutral"]
    confidence: int
    regime: str = ""
    support: tuple = ()
    resistance: tuple = ()
    invalidation: Decimal | None = None
    evidence: tuple = ()

    def __post_init__(self) -> None:
        if self.bias not in {"bullish", "bearish", "neutral"}:
            raise PolicyViolation("Market bias must be bullish/bearish/neutral.")
        _confidence(self.confidence, "market")


@dataclass(frozen=True)
class NewsView:
    bias: Literal["bullish", "bearish", "neutral"]
    confidence: int
    catalyst: str = ""
    catalyst_strength: int = 0  # 0-100
    expected_days: int = 0
    events: tuple = ()

    def __post_init__(self) -> None:
        if self.bias not in {"bullish", "bearish", "neutral"}:
            raise PolicyViolation("News bias must be bullish/bearish/neutral.")
        _confidence(self.confidence, "news")
        if not 0 <= self.catalyst_strength <= 100:
            raise PolicyViolation("Catalyst strength must be 0-100.")


@dataclass(frozen=True)
class MacroView:
    regime: Literal["RISK_ON", "RISK_OFF", "CHOP", "HIGH_VOLATILITY", "EVENT_RISK", "UNKNOWN"]
    confidence: int
    evidence: tuple = ()

    def __post_init__(self) -> None:
        if self.regime not in {"RISK_ON", "RISK_OFF", "CHOP", "HIGH_VOLATILITY",
                               "EVENT_RISK", "UNKNOWN"}:
            raise PolicyViolation("Unknown macro regime.")
        _confidence(self.confidence, "macro")


@dataclass(frozen=True)
class DebateOpinion:
    side: Literal["bull", "bear", "red"]
    verdict: str
    confidence: int
    thesis: str = ""
    invalidation: str = ""
    evidence: tuple = ()

    def __post_init__(self) -> None:
        if self.side not in {"bull", "bear", "red"}:
            raise PolicyViolation("Debate side must be bull/bear/red.")
        _confidence(self.confidence, self.side)


@dataclass(frozen=True)
class Judgment:
    verdict: Verdict
    confidence: int
    agreement_score: int
    bull_score: int
    bear_score: int
    reasoning_summary: str = ""
    recommended_contract: str | None = None


@dataclass(frozen=True)
class DeskInput:
    symbol: str
    side_hint: Literal["buy", "sell"] = "buy"
    market: MarketView | None = None
    news: NewsView | None = None
    macro: MacroView | None = None
    bull: DebateOpinion | None = None
    bear: DebateOpinion | None = None
    red: DebateOpinion | None = None


def _confidence(value: int, name: str) -> int:
    if not 0 <= value <= 100:
        raise PolicyViolation(f"{name} confidence must be 0-100.")
    return value


def rule_judge(market: MarketView | None, news: NewsView | None,
               bull: DebateOpinion | None, bear: DebateOpinion | None,
               red: DebateOpinion | None) -> Judgment:
    """Deterministic judge. A present red-team flag at confidence>=70 vetoes
    the trade (returns NO_TRADE with the veto strength as confidence);
    absent red proceeds to scoring."""
    bull_c = bull.confidence if bull else 50
    bear_c = bear.confidence if bear else 50
    if red is not None and red.confidence >= 70:
        return Judgment("NO_TRADE", red.confidence, 100 - abs(bull_c - bear_c),
                        bull_c, bear_c,
                        f"Red team blocked: {red.thesis}")
    scores = []
    if market is not None:
        scores.append(market.confidence if market.bias == "bullish" else -market.confidence
                      if market.bias == "bearish" else 0)
    if news is not None:
        scores.append(news.confidence if news.bias == "bullish" else -news.confidence
                      if news.bias == "bearish" else 0)
    if bull is not None:
        scores.append(bull.confidence)
    if bear is not None:
        scores.append(-bear.confidence)
    total = sum(scores)
    bull_score = max(0, total)
    bear_score = max(0, -total)
    agreement = 100 - min(100, abs((bull.confidence if bull else 50)
                                   - (bear.confidence if bear else 50)))
    if total >= 120:
        verdict: Verdict = "STRONG_CALL"
    elif total >= 60:
        verdict = "CALL"
    elif total <= -120:
        verdict = "STRONG_PUT"
    elif total <= -60:
        verdict = "PUT"
    else:
        verdict = "NO_TRADE"
    confidence = min(99, abs(total) // 2 + 40) if verdict != "NO_TRADE" else min(99, 100 - abs(total))
    return Judgment(verdict, confidence, agreement, bull_score, bear_score,
                    f"net={total} from {len(scores)} opinions")


def signal_confidence(decision: str) -> tuple[str, int]:
    """Map a TradingAgents rating to (direction, fixed confidence).

    Confidences are fixed and UNCALIBRATED (single-model debate output, not
    a scored candidate). They are recorded transparently as such; execution
    authority stays with the gated paper flow / RiskEngine, never the judge.
    """
    direction = decision.strip().lower()
    if direction in {"buy"}:
        return "bullish", 75
    if direction in {"overweight"}:
        return "bullish", 65
    if direction in {"sell"}:
        return "bearish", 75
    if direction in {"underweight"}:
        return "bearish", 65
    return "neutral", 50


def desk_input_from_signal(ticker: str, decision: str, provider: str = "",
                           analyst_reports: dict | None = None) -> DeskInput:
    """Advisory DeskInput from a single-model debate outcome.

    The bull opinion carries the decision; the bear/red opinions are
    explicitly synthetic counterweights (single-model limitation, data
    gaps), so the ledger never pretends at independent confirmation.
    Real analyst report text (when supplied) rides as evidence strings.
    """
    reports = analyst_reports or {}
    snippets = tuple(f"{name}: {str(text)[:500]}" for name, text in reports.items() if text)
    direction, conf = signal_confidence(decision)
    counter = max(30, 120 - conf - 10)
    if direction == "bullish":
        side_hint = "buy"
        market = MarketView("bullish", conf, evidence=(f"tradingagents:{decision}", provider, *snippets))
        bull = DebateOpinion("bull", "bullish", conf, thesis=f"Debate concluded {decision}")
        bear = DebateOpinion("bear", "bearish", counter,
                             thesis="Single-model debate; Yahoo sampled, no FRED macro")
    elif direction == "bearish":
        side_hint = "sell"
        market = MarketView("bearish", conf, evidence=(f"tradingagents:{decision}", provider, *snippets))
        bull = DebateOpinion("bull", "bullish", counter,
                             thesis="Single-model debate; upside surprise possible")
        bear = DebateOpinion("bear", "bearish", conf, thesis=f"Debate concluded {decision}")
    else:
        side_hint = "buy"
        market = MarketView("neutral", 50, evidence=(f"tradingagents:{decision}", provider, *snippets))
        bull = DebateOpinion("bull", "bullish", 45, thesis="No actionable edge")
        bear = DebateOpinion("bear", "bearish", 45, thesis="No actionable edge")
    red = DebateOpinion("red", "challenge", 35,
                        thesis="Automated pass: no independent red team yet")
    return DeskInput(symbol=ticker.upper(), side_hint=side_hint, market=market,  # type: ignore[arg-type]
                     bull=bull, bear=bear, red=red)


def run_desk_analysis(*, database: CioDatabase, candidate_id: str,
                      desk_input: DeskInput, risk_engine=None,
                      proposal=None, today: date | None = None,
                      now_et: datetime | None = None,
                      market_open: bool | None = None) -> dict:
    """Full pipeline: ledger states, opinions, judge, risk verdict. No orders.

    Returns {"judgment": Judgment, "risk": RiskDecision, "outcome": str}.
    Buys proceed to risk only on CALL/STRONG_CALL; sells only on
    PUT/STRONG_PUT; otherwise NO_TRADE is recorded and returned.
    """
    today = today or date.today()
    if desk_input.symbol.upper() != (proposal.symbol.upper() if proposal is not None
                                     else desk_input.symbol.upper()):
        raise PolicyViolation("Desk input symbol disagrees with the risk proposal.")
    if proposal is not None and desk_input.side_hint != proposal.side:
        raise PolicyViolation("Desk side_hint disagrees with the risk proposal side.")
    database.record_candidate(candidate_id, desk_input.symbol,
                              desk_input.side_hint,
                              {"source": "desk" if proposal is not None else "desk-advisory",
                               "executor": "risk-engine" if proposal is not None else "paper_flow"})
    database.update_candidate_status(candidate_id, "researching")
    views = (("market", desk_input.market), ("news", desk_input.news),
             ("macro", desk_input.macro))
    for agent, view in views:
        if view is None:
            continue
        database.record_opinion(
            candidate_id, agent,
            str(getattr(view, "bias", getattr(view, "regime", ""))),
            _confidence(view.confidence, agent),
            {"evidence": list(getattr(view, "evidence", ())),
             "events": list(getattr(view, "events", ())),
             "regime": getattr(view, "regime", ""),
             "catalyst": getattr(view, "catalyst", "")})
    database.update_candidate_status(candidate_id, "debating")
    for opinion in (desk_input.bull, desk_input.bear, desk_input.red):
        if opinion is None:
            continue
        database.record_opinion(
            candidate_id, opinion.side, opinion.verdict,
            _confidence(opinion.confidence, opinion.side),
            {"thesis": opinion.thesis, "invalidation": opinion.invalidation,
             "evidence": list(opinion.evidence)})
    judgment = rule_judge(desk_input.market, desk_input.news, desk_input.bull,
                          desk_input.bear, desk_input.red)
    database.record_opinion(candidate_id, "judge", judgment.verdict,
                            judgment.confidence,
                            {"agreement": judgment.agreement_score,
                             "summary": judgment.reasoning_summary})
    want_buy = ((proposal.side if proposal is not None else desk_input.side_hint) == "buy")
    side_ok = ((want_buy and judgment.verdict in {"CALL", "STRONG_CALL"})
               or (not want_buy and judgment.verdict in {"PUT", "STRONG_PUT"}))
    if not side_ok:
        database.update_candidate_status(candidate_id, "rejected")
        return {"judgment": judgment, "risk": None, "outcome": "NO_TRADE"}
    if risk_engine is None or proposal is None:
        # Advisory: debate recorded; execution authority stays with the
        # gated paper flow (no risk row — the UI shows NO RISK RUN).
        database.update_candidate_status(candidate_id, "approved")
        return {"judgment": judgment, "risk": None, "outcome": f"ADVISORY_{judgment.verdict}"}
    database.update_candidate_status(candidate_id, "risk_review")
    risk = risk_engine.check(proposal, today=today, now_et=now_et,
                             market_open=market_open, database=database)
    database.record_risk_decision(f"{candidate_id}:risk", candidate_id,
                                  approved=risk.approved,
                                  failed_rules=[r.name for r in risk.failed],
                                  results=[{"name": r.name, "passed": r.passed,
                                            "skipped": r.skipped, "reason": r.reason}
                                           for r in risk.results])
    database.update_candidate_status(candidate_id, "approved" if risk.approved else "rejected")
    return {"judgment": judgment, "risk": risk,
            "outcome": "APPROVED" if risk.approved else "RISK_REJECTED"}
