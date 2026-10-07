"""Phase 2 PR 2: RiskEngine facade tests. Fail-closed contract."""
from __future__ import annotations

import ast
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.risk import PortfolioState, RiskLimits
from robinhood_tools.risk_engine import RiskEngine, TradeProposal
from robinhood_tools.safety import DataQuality
from robinhood_tools.universe import Sp500Snapshot

ET = ZoneInfo("America/New_York")


def snapshot(*symbols: str) -> Sp500Snapshot:
    return Sp500Snapshot(
        symbols=frozenset(symbols),
        as_of=datetime.now(ET).isoformat(),
        source_url="https://example.com/sp500")


def portfolio() -> PortfolioState:
    return PortfolioState(
        equity=Decimal("100000"), cash=Decimal("90000"),
        position_weights={}, sector_weights={},
        pending_approvals=0, approved_capital_today=Decimal("0"),
        buying_power=Decimal("90000"))


def engine(**overrides) -> RiskEngine:
    params = dict(risk_limits=RiskLimits(max_order_value=Decimal("500"),
                                         max_symbol_exposure=Decimal("500"),
                                         max_open_positions=10,
                                         max_order_pct_avg_volume=Decimal("1")),
                  earnings_blackout_days=5, min_confidence=75)
    params.update(overrides)
    return RiskEngine(**params)


def proposal(**overrides) -> TradeProposal:
    params = dict(
        symbol="NVDA", side="buy", quantity=Decimal("2"),
        limit_price=Decimal("230.00"), intended_price=Decimal("230.00"),
        universe_snapshot=snapshot("NVDA", "AAPL"),
        earnings_date=date(2026, 11, 20),
        spread_pct=Decimal("0.001"),
        avg_daily_dollar_volume=Decimal("1000000000"),
        order_value=Decimal("460"), portfolio=portfolio(), sector="Technology",
        score=95, market_regime="risk_on", confidence=80,
        data_quality=DataQuality(True, True, True, True, True, True))
    params.update(overrides)
    return TradeProposal(**params)


def tuesday_noon() -> datetime:
    return datetime(2026, 10, 6, 12, 0, tzinfo=ET)  # Tuesday


class RiskEngineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = CioDatabase(Path(self.temp.name) / "cio.db")

    def tearDown(self):
        self.temp.cleanup()

    def check(self, prop, eng=None, **kwargs):
        kwargs.setdefault("today", date(2026, 10, 6))
        kwargs.setdefault("now_et", tuesday_noon())
        kwargs.setdefault("market_open", True)
        kwargs.setdefault("database", self.db)
        return (eng or engine()).check(prop, **kwargs)

    def test_approve_clean_proposal(self):
        decision = self.check(proposal())
        self.assertTrue(decision.approved, [r for r in decision.results if not r.passed])
        decision.raise_on_fail(proposal())

    def test_each_layer_can_fail(self):
        cases = [
            ("mode", dict(), dict(live_trading_enabled=True)),
            ("mode", dict(), dict(paper_trading_enabled=False)),
            ("mode", dict(), dict(human_approval_required=True)),
            ("mode", dict(), dict(mode="research_only")),
            ("session", dict(), dict()),
            ("universe", dict(universe_snapshot=snapshot("AAPL")), dict()),
            ("universe", dict(universe_snapshot=None), dict()),
            ("earnings", dict(earnings_date=date(2026, 10, 8)), dict()),
            ("earnings", dict(earnings_date=None), dict()),
            ("news", dict(material_negative_news=True), dict()),
            ("data_quality", dict(data_quality=DataQuality(True, False, True, True, True, True)), dict()),
            ("data_quality", dict(data_quality=None), dict()),
            ("regime", dict(score=80), dict()),
            ("regime", dict(score=None), dict()),
            ("sizing", dict(order_value=Decimal("600")), dict()),
            ("sizing", dict(portfolio=None), dict()),
            ("shape", dict(limit_price=Decimal("231.00")), dict()),
            ("shape", dict(intended_price=None), dict()),
            ("confidence", dict(confidence=70), dict()),
            ("confidence", dict(confidence=None), dict()),
            ("state", dict(), dict()),
        ]
        for rule, p_over, e_over in cases:
            with self.subTest(rule=rule):
                if rule == "session":
                    decision = self.check(proposal(), market_open=False)
                elif rule == "state":
                    self.db.set_emergency_kill(True)
                    decision = self.check(proposal())
                    self.db.set_emergency_kill(False)
                else:
                    decision = (engine(**e_over)).check(
                        proposal(**p_over), today=date(2026, 10, 6),
                        now_et=tuesday_noon(), market_open=True, database=self.db)
                self.assertFalse(decision.approved, rule)
                self.assertIn(rule, [r.name for r in decision.failed], rule)
                with self.assertRaises(PolicyViolation):
                    decision.raise_on_fail(proposal(**p_over))

    def test_session_requires_timestamp_and_clock(self):
        for kwargs in (dict(now_et=None), dict(now_et=datetime(2026, 10, 6, 12, 0)),
                       dict(market_open=None), dict(market_open=False)):
            with self.subTest(kwargs=kwargs):
                decision = self.check(proposal(), **kwargs)
                self.assertIn("session", [r.name for r in decision.failed])

    def test_weekend_blocked(self):
        saturday = datetime(2026, 10, 10, 12, 0, tzinfo=ET)
        decision = self.check(proposal(), today=date(2026, 10, 10), now_et=saturday)
        self.assertIn("session", [r.name for r in decision.failed])

    def test_internal_error_fails_closed(self):
        decision = self.check(proposal(), eng=engine(earliest_entry_et="bogus"))
        self.assertFalse(decision.approved)
        self.assertIn("session", [r.name for r in decision.failed])

    def test_unknown_regime_fails_closed(self):
        decision = self.check(proposal(market_regime="unknown"))
        self.assertIn("regime", [r.name for r in decision.failed])

    def test_sell_side_skips_buy_rules_but_stays_gated(self):
        sell = proposal(side="sell", universe_snapshot=None, earnings_date=None,
                        score=None, market_regime=None, confidence=None,
                        data_quality=None, portfolio=None,
                        avg_daily_dollar_volume=None, intended_price=None)
        decision = self.check(sell)
        skipped = {r.name for r in decision.results if r.skipped}
        self.assertTrue({"universe", "earnings", "data_quality", "regime",
                         "sizing", "confidence"} <= skipped)
        self.assertTrue(decision.approved)
        # ...but mode/session/shape/state still bind sells
        bad = proposal(side="sell", order_type="market", universe_snapshot=None,
                       earnings_date=None, score=None, market_regime=None,
                       confidence=None, data_quality=None, portfolio=None,
                       avg_daily_dollar_volume=None, intended_price=None)
        decision = self.check(bad)
        self.assertIn("shape", [r.name for r in decision.failed])

    def test_no_ledger_no_trade(self):
        decision = engine().check(proposal(), today=date(2026, 10, 6),
                                  now_et=tuesday_noon(), market_open=True,
                                  database=None)
        self.assertIn("state", [r.name for r in decision.failed])

    def test_paper_from_settings_wires_flags(self):
        from robinhood_tools.runtime import build_settings

        settings = build_settings()
        eng = RiskEngine.paper_from_settings(settings)
        self.assertEqual(eng.mode, "paper_auto")
        self.assertFalse(eng.human_approval_required)
        self.assertEqual(eng.entry_minimum_scores["risk_on"], 90)
        decision = eng.check(proposal(), today=date(2026, 10, 6),
                             now_et=tuesday_noon(), market_open=True,
                             database=self.db)
        self.assertTrue(decision.approved)

    def test_raise_message_names_symbol_side_rule(self):
        decision = self.check(proposal(confidence=10))
        with self.assertRaises(PolicyViolation) as ctx:
            decision.raise_on_fail(proposal(confidence=10))
        self.assertIn("NVDA", str(ctx.exception))
        self.assertIn("confidence", str(ctx.exception))

    def test_no_llm_or_network_in_engine(self):
        tree = ast.parse(Path("robinhood_tools/risk_engine.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        banned = {"urllib", "socket", "requests", "httpx", "tradingagents",
                  "langchain", "openai", "anthropic"}
        self.assertEqual(imported & banned, set())


if __name__ == "__main__":
    unittest.main()
