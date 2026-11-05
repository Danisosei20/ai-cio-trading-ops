"""Regression guards for the autonomous auto-trader.

2026-11-05 live defects:
  - MAA: "portfolio risk check failed: UnboundLocalError" — `cap` was read by
    the portfolio-risk gate before it was assigned inside the ticker loop,
    which made it an unbound local for the whole function.
  - APP/CPAY/HD/TPR/ZTS: "research exceeded 600s" — the OpenAI SDK's default
    600s read timeout meant one stalled provider call consumed the entire
    per-ticker budget. The trader now forwards an explicit per-request
    `timeout` into the LLM client factory.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTO = ROOT / "scripts" / "tradingagents_paper_auto.py"
FACTORY = (ROOT.parent / "TradingAgents" / "tradingagents" / "llm_clients"
           / "factory.py")


def _ticker_loop_line(tree: ast.AST) -> int:
    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and node.target.id == "ticker"):
            return node.lineno
    raise AssertionError("ticker loop not found")


def _assigned_lines(tree: ast.AST, name: str) -> list[int]:
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]


class AutoTraderGuardTests(unittest.TestCase):
    def setUp(self):
        self.source = AUTO.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.loop_line = _ticker_loop_line(self.tree)

    def test_cap_assigned_before_ticker_loop(self):
        """`cap` must exist before the loop that reads it (MAA UnboundLocalError)."""
        lines = _assigned_lines(self.tree, "cap")
        self.assertTrue(lines, "cap is never assigned")
        self.assertLess(
            min(lines), self.loop_line,
            "cap must be assigned before the ticker loop; assigning it only "
            "inside the loop makes it an unbound local at the risk gate",
        )

    def test_risk_controller_initialised_before_loop(self):
        """`_ctrl` must be bound before the loop so a failed check leaves None."""
        lines = _assigned_lines(self.tree, "_ctrl")
        self.assertTrue(lines, "_ctrl is never assigned")
        self.assertLess(min(lines), self.loop_line)

    def test_exits_bypass_new_risk_gate(self):
        """Only BUY signals call the portfolio risk gate; exits are never blocked."""
        self.assertIn('if not rec.get("reason") and sig in BUY_SIGNALS:', self.source)

    def test_llm_timeout_is_forwarded_by_factory(self):
        src = FACTORY.read_text(encoding="utf-8")
        self.assertIn('config.get("timeout")', src)
        self.assertIn('kwargs["timeout"]', src)

    def test_research_sets_explicit_llm_timeout(self):
        self.assertIn('config["timeout"] = args.llm_timeout', self.source)
        self.assertIn('config["llm_max_retries"] = args.llm_max_retries',
                      self.source)


if __name__ == "__main__":
    unittest.main()
