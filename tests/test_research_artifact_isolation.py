"""Research and News stations must not overwrite each other's decisions."""
import unittest

from scripts import tradingagents_research


class ResearchArtifactIsolationTests(unittest.TestCase):
    def test_analyst_tagged_directory(self):
        p = tradingagents_research.artifact_dir("NVDA", "2026-11-23", ["market", "news"])
        self.assertIn("NVDA", str(p))
        self.assertIn("2026-11-23", str(p))
        self.assertNotEqual(p, tradingagents_research.artifact_dir(
            "NVDA", "2026-11-23", ["news"]))

    def test_news_and_research_are_distinct(self):
        research = tradingagents_research.artifact_dir(
            "NVDA", "2026-11-23", ["market", "news", "fundamentals"])
        news = tradingagents_research.artifact_dir("NVDA", "2026-11-23", ["news"])
        self.assertNotEqual(research, news)

    def test_stable_for_same_analysts(self):
        a = tradingagents_research.artifact_dir("NVDA", "2026-11-23", ["news"])
        b = tradingagents_research.artifact_dir("NVDA", "2026-11-23", ["news"])
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
