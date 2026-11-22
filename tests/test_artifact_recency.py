"""Glob artifacts must be selected by recency, not by filename order.

A research stage writes a fresh artifact for one ticker while older tickers
remain on disk. Selecting the lexicographically-last path makes a successful
run look stale and flips a required stage to warn/halt.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.desk_orchestrator import _artifact_path, _read_artifact


class ArtifactRecencyTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        pattern = "outputs/paper/tradingagents/**/auto.json"
        self.fresh = self.root / "outputs/paper/tradingagents/AAA/2026-11-22/auto.json"
        self.old = self.root / "outputs/paper/tradingagents/ZZZ/2026-11-01/auto.json"
        for path, stamp in ((self.fresh, "2026-11-22T10:00:00-05:00"),
                            (self.old, "2026-11-01T10:00:00-05:00")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"ticker": path.parent.parent.name,
                                        "generated_at": stamp, "status": "ok"}),
                            encoding="utf-8")
        # ZZZ sorts last but is older on disk.
        os.utime(self.old, (1_700_000_000, 1_700_000_000))
        os.utime(self.fresh, (1_800_000_000, 1_800_000_000))
        self.pattern = pattern

    def tearDown(self):
        self.t.cleanup()

    def test_glob_selects_newest_mtime_not_lexicographic_last(self):
        self.assertEqual(_artifact_path(self.root, self.pattern), self.fresh)

    def test_read_artifact_uses_the_same_selection(self):
        self.assertEqual(_read_artifact(self.root, self.pattern),
                         json.loads(self.fresh.read_text(encoding="utf-8")))

    def test_tie_breaks_lexicographically_for_determinism(self):
        os.utime(self.old, (1_800_000_000, 1_800_000_000))
        self.assertEqual(_artifact_path(self.root, self.pattern), self.old)
