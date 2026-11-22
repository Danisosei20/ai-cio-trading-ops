"""Contract tests: every orchestrator stage must point at a real producer.

The runner is fail-closed, so a stage whose ``primary_artifact`` names a file
that no script writes is rated ``fail`` and halts the whole 09:45 run. These
tests pin the artifact strings to the layouts the real producers write and
exercise the new bar reader with a stubbed yfinance module (no network).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import desk_bar_reader  # noqa: E402
from scripts.desk_stage_agents import STAGES  # noqa: E402

EXPECTED_ARTIFACTS = {
    "ZoneAnalyst": "outputs/zones/*/*.json",
    "ResearchFleet": "outputs/paper/tradingagents/**/auto.json",
    "BarReader": "outputs/orchestrator/<run_id>/bars_<symbol>.json",
}


def _search_roots() -> list[Path]:
    """Repo root plus the main checkout when running from a git worktree.

    Worktrees share tracked files but not the (gitignored) ``outputs/`` tree,
    which lives in the primary checkout. The runner resolves globs against its
    own root, so the pattern contract is checked against both locations.
    """
    roots = [ROOT]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
        main = Path(out).parent
        if main.is_dir() and main != ROOT and (main / "scripts" / "desk_stage_agents.py").is_file():
            roots.append(main)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return roots


def _resolve(value: str) -> str:
    return value.replace("<run_id>", "r-test").replace("<symbol>", "NVDA")


def _matchesExistingFile(pattern: str) -> bool:
    resolved = _resolve(pattern)
    for root in _search_roots():
        try:
            if any(p.is_file() for p in Path(root).glob(resolved)):
                return True
        except (OSError, ValueError):
            continue
    return False


def _normalizedSource(path: Path) -> str:
    """Collapse ``ROOT / "outputs" / "zones"`` style source into a path string."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return (text.replace('"', "").replace("'", "")
                .replace(" / ", "/").replace("/ ", "/").replace(" /", "/"))


def _producerWrites(spec) -> bool:
    """Fallback proof: the stage's argv names a script that writes this path."""
    producer = ROOT / spec.argv[0]
    if not producer.is_file():
        return False
    source = _normalizedSource(producer)
    artifact = spec.primary_artifact
    if "--out" in spec.argv and spec.argv[spec.argv.index("--out") + 1] == artifact:
        return "--out" in source
    parts = [p for p in artifact.split("/")
             if p and not any(ch in p for ch in "*?<>")]
    for size in range(len(parts), 1, -1):
        if "/".join(parts[:size]) in source:
            return True
    return False


class StubTicker:
    """Minimal yfinance stand-in: records the call and returns fixed bars."""

    calls: list[tuple[str, str, str]] = []
    rows = [
        (datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc),
         {"Open": 10.0, "High": 11.0, "Low": 9.5, "Close": 10.5, "Volume": 100}),
    ]
    error: Exception | None = None

    def __init__(self, symbol: str):
        self.symbol = symbol

    def history(self, period: str, interval: str):
        type(self).calls.append((self.symbol, period, interval))
        if self.error is not None:
            raise self.error
        return _StubHistory(type(self).rows)


class _StubHistory:
    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def iterrows(self):
        return iter(self._rows)


def _stubModule() -> types.ModuleType:
    module = types.ModuleType("yfinance")
    module.Ticker = StubTicker
    return module


class ProducerArtifactTests(unittest.TestCase):
    def test_every_stage_has_a_producer_script_and_an_artifact(self):
        for spec in STAGES:
            with self.subTest(agent=spec.agent):
                self.assertTrue(spec.primary_artifact)
                self.assertTrue(spec.argv)
                self.assertTrue((ROOT / spec.argv[0]).is_file(),
                                f"{spec.agent}: producer {spec.argv[0]} missing")

    def test_every_stage_artifact_exists_or_is_written_by_its_producer(self):
        for spec in STAGES:
            with self.subTest(agent=spec.agent):
                self.assertTrue(
                    _matchesExistingFile(spec.primary_artifact) or _producerWrites(spec),
                    f"{spec.agent}: artifact {spec.primary_artifact!r} has no real producer",
                )

    def test_three_stages_point_at_real_producer_paths(self):
        by_agent = {spec.agent: spec for spec in STAGES}
        for agent, artifact in EXPECTED_ARTIFACTS.items():
            with self.subTest(agent=agent):
                self.assertIn(agent, by_agent)
                self.assertEqual(by_agent[agent].primary_artifact, artifact)

        bar_reader = by_agent["BarReader"]
        self.assertEqual(bar_reader.argv[0], "scripts/desk_bar_reader.py")
        self.assertIn("--out", bar_reader.argv)
        self.assertEqual(
            bar_reader.argv[bar_reader.argv.index("--out") + 1],
            bar_reader.primary_artifact,
        )

    def test_zone_and_research_globs_match_existing_producer_files(self):
        by_agent = {spec.agent: spec for spec in STAGES}
        for agent in ("ZoneAnalyst", "ResearchFleet"):
            with self.subTest(agent=agent):
                artifact = by_agent[agent].primary_artifact
                self.assertIn("*", artifact)
                self.assertTrue(_matchesExistingFile(artifact),
                                f"{agent}: glob {artifact!r} matches no real artifact")


class BarReaderTests(unittest.TestCase):
    def setUp(self):
        StubTicker.calls = []
        StubTicker.error = None

    def test_script_exists(self):
        self.assertTrue((ROOT / "scripts" / "desk_bar_reader.py").is_file())

    def test_stubbed_yfinance_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "orchestrator/r-test/bars_NVDA.json"
            with mock.patch.dict(sys.modules, {"yfinance": _stubModule()}):
                rc = desk_bar_reader.main(["--out", str(out), "--symbol", "SPY",
                                           "--sessions", "3", "--interval", "5m"])
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(doc["symbol"], "SPY")
            self.assertEqual(doc["source"], "yfinance")
            self.assertTrue(doc["generated_at"])
            self.assertEqual(len(doc["bars"]), 1)
            bar = doc["bars"][0]
            for key in ("ts", "open", "high", "low", "close", "volume"):
                self.assertIn(key, bar)
            self.assertEqual(bar["close"], 10.5)
            self.assertEqual(StubTicker.calls, [("SPY", "3d", "5m")])

    def test_fetch_error_writes_error_artifact_and_nonzero(self):
        StubTicker.error = RuntimeError("stubbed fetch failure")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bars_NVDA.json"
            with mock.patch.dict(sys.modules, {"yfinance": _stubModule()}):
                rc = desk_bar_reader.main(["--out", str(out)])
            self.assertNotEqual(rc, 0)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(doc["symbol"], "NVDA")
            self.assertEqual(doc["bars"], [])
            self.assertEqual(doc["source"], "yfinance")
            self.assertTrue(doc["generated_at"])
            self.assertIn("stubbed fetch failure", doc["error"])


if __name__ == "__main__":
    unittest.main()
