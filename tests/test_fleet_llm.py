import json
import tempfile
import unittest
from pathlib import Path

from scripts.fleet_llm import fleet_llm


class FleetLLMTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        self.evidence = [{"label": "screener", "symbol": "NVDA", "score": 2.1},
                         {"label": "liquid", "crowded_longs": 3}]
        self.prompts = []

    def tearDown(self):
        self.t.cleanup()

    def fake(self, result):
        def call(prompt):
            self.prompts.append(prompt)
            return result
        return call

    def test_ok_payload_and_citations(self):
        doc = fleet_llm(self.root, "screen", self.evidence,
                        call_llm=self.fake({"analysis": "Strong shortlist",
                                            "confidence": "high", "cited": [0]}))
        self.assertEqual(doc["status"], "ok")
        self.assertEqual(doc["citations"][0]["label"], "screener")
        artifact = self.root / doc["artifact"]
        self.assertTrue(artifact.exists())
        self.assertEqual(json.loads(artifact.read_text(encoding="utf-8"))["status"], "ok")

    def test_invalid_citations_dropped(self):
        doc = fleet_llm(self.root, "liquid", self.evidence,
                        call_llm=self.fake({"analysis": "Crowded", "cited": [0, 5, -1, 1, 1]}))
        self.assertEqual([c["index"] for c in doc["citations"]], [0, 1])

    def test_llm_failure_is_error(self):
        def boom(prompt):
            raise RuntimeError("no api")
        doc = fleet_llm(self.root, "verify", self.evidence, call_llm=boom)
        self.assertEqual(doc["status"], "unavailable")
        self.assertIn("no api", doc["reason"])

    def test_empty_evidence_is_error(self):
        doc = fleet_llm(self.root, "screen", [], call_llm=self.fake({"analysis": "x"}))
        self.assertEqual(doc["status"], "unavailable")

    def test_prompt_forbids_generated_numbers(self):
        fleet_llm(self.root, "report", self.evidence,
                  call_llm=self.fake({"analysis": "ok"}))
        self.assertIn("Never invent prices", self.prompts[0])
        self.assertIn("Do not recommend a trade", self.prompts[0])

    def test_empty_analysis_is_error(self):
        doc = fleet_llm(self.root, "news", self.evidence, call_llm=self.fake({}))
        self.assertEqual(doc["status"], "unavailable")

    def test_non_dict_return_is_error(self):
        for bad in (["x"], "text", None, 42):
            doc = fleet_llm(self.root, "screen", self.evidence, call_llm=lambda p: bad)
            self.assertEqual(doc["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
