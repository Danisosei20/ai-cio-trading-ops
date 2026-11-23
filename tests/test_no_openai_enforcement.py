"""The no-OpenAI policy must be enforced in code, not only in docs."""
import os
import unittest
from unittest import mock

from scripts import tradingagents_research, tradingagents_paper_auto


class NoOpenAIEnforcementTests(unittest.TestCase):
    def test_research_rejects_openai_provider_flag(self):
        with self.assertRaises(SystemExit):
            tradingagents_research.parse_args([
                "--ticker", "NVDA", "--date", "2026-11-23", "--provider", "openai"])

    def test_research_rejects_openai_from_env(self):
        env = dict(os.environ, TRADINGAGENTS_LLM_PROVIDER="openai")
        with mock.patch.dict(os.environ, env, clear=True), self.assertRaises(SystemExit):
            tradingagents_research.parse_args(["--ticker", "NVDA", "--date", "2026-11-23"])

    def test_research_accepts_nvidia(self):
        a = tradingagents_research.parse_args([
            "--ticker", "NVDA", "--date", "2026-11-23", "--provider", "nvidia"])
        self.assertEqual(a.provider, "nvidia")

    def test_paper_auto_rejects_openai(self):
        with self.assertRaises(SystemExit):
            tradingagents_paper_auto.parse_args(["--provider", "openai"])

    def test_paper_auto_accepts_nvidia(self):
        a = tradingagents_paper_auto.parse_args(["--provider", "nvidia"])
        self.assertEqual(a.provider, "nvidia")


if __name__ == "__main__":
    unittest.main()
