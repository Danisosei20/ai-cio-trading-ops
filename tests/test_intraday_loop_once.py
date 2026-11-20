"""The intraday loop must be rehearsable outside the session window.

2026-11-16 rehearsal: `intraday_loop.py --once --force --dry-run` at
22:39 ET logged "end of scan window reached" and did nothing - three
months after the task's registration was repaired, the loop's wiring
could not be verified after hours. --once means "run one cycle now", so
the end-of-window check must yield to it (the market-open check already
did, via --force).
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "intraday_loop.py").read_text(encoding="utf-8")


class OnceBypassesTheWindowTest(unittest.TestCase):
    def test_the_window_check_yields_to_once(self):
        tree = ast.parse(SRC)
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            names = {n.id for n in ast.walk(node.test)
                     if isinstance(n, ast.Name)}
            attrs = {n.attr for n in ast.walk(node.test)
                     if isinstance(n, ast.Attribute)}
            if "end_dt" in names and "once" in attrs:
                found = True
        self.assertTrue(found,
                        "the end-of-window check must yield to --once, or "
                        "the loop cannot be rehearsed outside the session")

    def test_force_still_bypasses_the_market_check(self):
        # --force is how a rehearsal runs with the market closed; keep it.
        self.assertIn("args.force", SRC)

    def test_the_rehearsal_path_is_documented_in_the_source(self):
        self.assertIn("--once", SRC)


if __name__ == "__main__":
    unittest.main()