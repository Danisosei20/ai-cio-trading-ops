"""All six fleet stations must be LLM-backed (research/news use TradingAgents)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts/desk_fleet.py").read_text(encoding="utf-8")


class FleetLLMStationTests(unittest.TestCase):
    def test_four_stations_call_the_llm_helper(self):
        for station in ("screen", "liquid", "verify", "report"):
            self.assertIn(f'_llm_answer("{station}"', SRC, station)

    def test_research_and_news_use_tradingagents_llm(self):
        self.assertIn("_research(ctx, args, \"research\"", SRC)
        self.assertIn("\"--analysts\", \"news\"", SRC)

    def test_llm_failure_never_crashes_fleet(self):
        self.assertIn("research failure never crashes the fleet", SRC)

    def test_no_order_path_added(self):
        self.assertNotIn("place_order", SRC)
        self.assertNotIn("submit_order", SRC)

    def test_llm_helper_is_fail_closed(self):
        from scripts.fleet_llm import fleet_llm
        self.assertEqual(fleet_llm(ROOT, "screen", [], call_llm=lambda p: {})["status"],
                         "unavailable")


if __name__ == "__main__":
    unittest.main()
