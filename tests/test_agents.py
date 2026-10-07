"""Phase 3 PR 1: desk pipeline tests (fixtures only, no LLM/network/broker)."""
from __future__ import annotations

import ast
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from robinhood_tools.agents import (DebateOpinion, DeskInput, MacroView,
                                    MarketView, NewsView, rule_judge,
                                    run_desk_analysis)
from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.risk import PortfolioState, RiskLimits
from robinhood_tools.risk_engine import RiskEngine, TradeProposal
from robinhood_tools.safety import DataQuality
from robinhood_tools.universe import Sp500Snapshot

ET = ZoneInfo("America/New_York")


def strong_bull() -> DeskInput:
    return DeskInput(
        symbol="NVDA",
        market=MarketView("bullish", 82, regime="uptrend",
                          support=(220,), resistance=(238,),
                          evidence=("price>10EMA>50SMA", "MACD hist expanding")),
        news=NewsView("bullish", 71, catalyst="AI demand", catalyst_strength=70,
                      expected_days=20, events=("analyst upgrades",)),
        macro=MacroView("RISK_ON", 59, evidence=("QQQ uptrend",)),
        bull=DebateOpinion("bull", "bullish", 88, thesis="VWAP reclaimed, demand holds",
                           invalidation="close below 227.80"),
        bear=DebateOpinion("bear", "bearish", 39, thesis="resistance near 238"),
        red=DebateOpinion("red", "challenge", 48, thesis="mild MACD non-confirmation"))


def buy_proposal(snapshot: Sp500Snapshot) -> TradeProposal:
    portfolio = PortfolioState(
        equity=Decimal("100000"), cash=Decimal("90000"),
        position_weights={}, sector_weights={}, pending_approvals=0,
        approved_capital_today=Decimal("0"), buying_power=Decimal("90000"))
    return TradeProposal(
        symbol="NVDA", side="buy", quantity=Decimal("2"),
        limit_price=Decimal("230.00"), intended_price=Decimal("230.00"),
        universe_snapshot=snapshot, earnings_date=date(2026, 11, 20),
        spread_pct=Decimal("0.001"),
        avg_daily_dollar_volume=Decimal("1000000000"),
        order_value=Decimal("460"), portfolio=portfolio, sector="Technology",
        score=95, market_regime="risk_on", confidence=80,
        data_quality=DataQuality(True, True, True, True, True, True))


class DeskPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = CioDatabase(Path(self.temp.name) / "cio.db")
        self.snapshot = Sp500Snapshot(
            frozenset({"NVDA"}), datetime.now(ET).isoformat(), "https://example.com")
        self.engine = RiskEngine(
            risk_limits=RiskLimits(max_order_value=Decimal("500"),
                                   max_symbol_exposure=Decimal("500"),
                                   max_open_positions=10,
                                   max_order_pct_avg_volume=Decimal("1")))

    def tearDown(self):
        self.temp.cleanup()

    def analyze(self, candidate_id, desk_input, proposal):
        return run_desk_analysis(
            database=self.db, candidate_id=candidate_id, desk_input=desk_input,
            risk_engine=self.engine, proposal=proposal, today=date(2026, 10, 6),
            now_et=datetime(2026, 10, 6, 12, 0, tzinfo=ET), market_open=True)

    def test_strong_bull_approved(self):
        out = self.analyze("d1", strong_bull(), buy_proposal(self.snapshot))
        self.assertEqual(out["outcome"], "APPROVED")
        self.assertIn(out["judgment"].verdict, {"CALL", "STRONG_CALL"})
        self.assertTrue(out["risk"].approved)
        timeline = self.db.candidate_timeline("d1")
        self.assertEqual(timeline["candidate"]["status"], "approved")
        agents = [o["agent"] for o in timeline["opinions"]]
        self.assertEqual(agents, ["market", "news", "macro", "bull", "bear", "red", "judge"])
        self.assertEqual(len(timeline["risk_decisions"]), 1)

    def test_weak_case_no_trade_before_risk(self):
        weak = DeskInput(
            symbol="NVDA",
            market=MarketView("neutral", 50, evidence=("chop",)),
            bull=DebateOpinion("bull", "bullish", 45, thesis="maybe"),
            bear=DebateOpinion("bear", "bearish", 55, thesis="resistance"))
        out = self.analyze("d2", weak, buy_proposal(self.snapshot))
        self.assertEqual(out["outcome"], "NO_TRADE")
        self.assertIsNone(out["risk"])
        self.assertEqual(self.db.candidate_timeline("d2")["candidate"]["status"], "rejected")
        self.assertEqual(self.db.candidate_timeline("d2")["risk_decisions"], [])

    def test_red_team_veto(self):
        veto = DeskInput(
            symbol="NVDA",
            market=MarketView("bullish", 90, evidence=("strong trend",)),
            bull=DebateOpinion("bull", "bullish", 95, thesis="breakout"),
            bear=DebateOpinion("bear", "bearish", 20, thesis="nothing"),
            red=DebateOpinion("red", "challenge", 85,
                              thesis="earnings in 2 days, unverified chain"))
        judgment = rule_judge(veto.market, veto.news, veto.bull, veto.bear, veto.red)
        self.assertEqual(judgment.verdict, "NO_TRADE")
        out = self.analyze("d3", veto, buy_proposal(self.snapshot))
        self.assertEqual(out["outcome"], "NO_TRADE")

    def test_risk_rejection_recorded(self):
        poor = buy_proposal(self.snapshot)
        poor = TradeProposal(**{**poor.__dict__, "confidence": 10})
        out = self.analyze("d4", strong_bull(), poor)
        self.assertEqual(out["outcome"], "RISK_REJECTED")
        timeline = self.db.candidate_timeline("d4")
        self.assertEqual(timeline["candidate"]["status"], "rejected")
        self.assertFalse(timeline["risk_decisions"][0]["approved"])

    def test_sell_side_put_path(self):
        desk = DeskInput(
            symbol="NVDA", side_hint="sell",
            market=MarketView("bearish", 85, evidence=("breakdown",)),
            bear=DebateOpinion("bear", "bearish", 88, thesis="trend broken"),
            bull=DebateOpinion("bull", "bullish", 30, thesis="oversold bounce"))
        sell = TradeProposal(symbol="NVDA", side="sell", quantity=Decimal("2"),
                             limit_price=Decimal("230.00"))
        out = self.analyze("d5", desk, sell)
        self.assertEqual(out["outcome"], "APPROVED")
        self.assertIn(out["judgment"].verdict, {"PUT", "STRONG_PUT"})

    def test_bad_confidence_rejected_at_construction(self):
        with self.assertRaises(PolicyViolation):
            MarketView("bullish", 101)
        with self.assertRaises(PolicyViolation):
            DebateOpinion("bull", "bullish", -1)

    def test_mismatched_side_verdict_no_trade(self):
        # Bullish verdict but sell proposal -> NO_TRADE, no risk run
        desk = DeskInput(
            symbol="NVDA", side_hint="sell",
            market=MarketView("bullish", 90, evidence=("trend",)),
            bull=DebateOpinion("bull", "bullish", 90, thesis="up"))
        sell = TradeProposal(symbol="NVDA", side="sell", quantity=Decimal("2"),
                             limit_price=Decimal("230.00"))
        out = self.analyze("d6", desk, sell)
        self.assertEqual(out["outcome"], "NO_TRADE")
        self.assertIsNone(out["risk"])

    def test_red_boundary(self):
        base = dict(symbol="NVDA",
                    market=MarketView("bullish", 90, evidence=("trend",)),
                    bull=DebateOpinion("bull", "bullish", 90, thesis="up"),
                    bear=DebateOpinion("bear", "bearish", 10, thesis="none"))
        low = DeskInput(red=DebateOpinion("red", "challenge", 69, thesis="hmm"), **base)
        high = DeskInput(red=DebateOpinion("red", "challenge", 70, thesis="block"), **base)
        self.assertNotEqual(
            rule_judge(low.market, low.news, low.bull, low.bear, low.red).verdict, "NO_TRADE")
        vetoed = rule_judge(high.market, high.news, high.bull, high.bear, high.red)
        self.assertEqual(vetoed.verdict, "NO_TRADE")
        self.assertEqual(vetoed.confidence, 70)

    def test_side_hint_mismatch_rejected(self):
        with self.assertRaises(PolicyViolation):
            self.analyze("d7", strong_bull(),
                         TradeProposal(symbol="NVDA", side="sell",
                                       quantity=Decimal("2"),
                                       limit_price=Decimal("230.00")))

    def test_signal_mapping(self):
        from robinhood_tools.agents import desk_input_from_signal, signal_confidence

        self.assertEqual(signal_confidence("Buy"), ("bullish", 75))
        self.assertEqual(signal_confidence("Overweight"), ("bullish", 65))
        self.assertEqual(signal_confidence("Sell"), ("bearish", 75))
        self.assertEqual(signal_confidence("Underweight"), ("bearish", 65))
        self.assertEqual(signal_confidence("Hold"), ("neutral", 50))
        buy_in = desk_input_from_signal("nvda", "Overweight", "test")
        self.assertEqual(buy_in.symbol, "NVDA")
        self.assertEqual(buy_in.side_hint, "buy")
        self.assertEqual(buy_in.market.bias, "bullish")

    def test_advisory_path_records_no_risk(self):
        from robinhood_tools.agents import desk_input_from_signal, run_desk_analysis

        out = run_desk_analysis(
            database=self.db, candidate_id="adv1",
            desk_input=desk_input_from_signal("NVDA", "Overweight", "test"))
        self.assertEqual(out["outcome"], "ADVISORY_CALL")
        self.assertIsNone(out["risk"])
        timeline = self.db.candidate_timeline("adv1")
        self.assertEqual(timeline["candidate"]["status"], "approved")
        self.assertEqual(timeline["risk_decisions"], [])
        self.assertEqual(len(timeline["opinions"]), 5)  # market+bull+bear+red+judge

    def test_advisory_hold_no_trade(self):
        from robinhood_tools.agents import desk_input_from_signal, run_desk_analysis

        out = run_desk_analysis(
            database=self.db, candidate_id="adv2",
            desk_input=desk_input_from_signal("NVDA", "Hold", "test"))
        self.assertEqual(out["outcome"], "NO_TRADE")
        self.assertEqual(self.db.candidate_timeline("adv2")["candidate"]["status"], "rejected")

    def test_advisory_sell_put(self):
        from robinhood_tools.agents import desk_input_from_signal, run_desk_analysis

        out = run_desk_analysis(
            database=self.db, candidate_id="adv3",
            desk_input=desk_input_from_signal("NVDA", "Underweight", "test"))
        self.assertEqual(out["outcome"], "ADVISORY_PUT")
        self.assertIsNone(out["risk"])
        self.assertEqual(self.db.candidate_timeline("adv3")["candidate"]["status"], "approved")

    def test_duplicate_candidate_rejected(self):
        from robinhood_tools.agents import desk_input_from_signal, run_desk_analysis

        desk_input = desk_input_from_signal("NVDA", "Overweight", "test")
        run_desk_analysis(database=self.db, candidate_id="dup1", desk_input=desk_input)
        with self.assertRaises(PolicyViolation):
            run_desk_analysis(database=self.db, candidate_id="dup1", desk_input=desk_input)

    def test_no_broker_or_network_in_desk(self):
        tree = ast.parse(Path("robinhood_tools/agents.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        banned = {"urllib", "socket", "requests", "httpx", "tradingagents",
                  "langchain", "openai", "anthropic", "alpaca", "yfinance"}
        self.assertEqual(imported & banned, set())


if __name__ == "__main__":
    unittest.main()
