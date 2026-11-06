"""15:10 watch direction tests: the RISK_OFF put path (pure, no network).

Task 5 of 2026-11-05-downside-signal-paths. The screener's top-N is all
strength, so a one-day slide never flipped a 20-day lookback and the watch
never offered a put. Under RISK_OFF the feed switches to the weak-momentum
tail with puts pinned -- through `gated_candidates`, so the held filter and
the RSI cap cannot be dropped on the way.
"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
import unittest.mock as mock
from contextlib import redirect_stdout
from pathlib import Path


def load_module():
    spec = importlib.util.spec_from_file_location(
        "options_watch_trade", Path("scripts/options_watch_trade.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCR = load_module()
gated_candidates = SCR.gated_candidates
_regime = SCR._regime
weak_rows = SCR.weak_rows


class WeakFeedGateTest(unittest.TestCase):
    """Ruling 1: the put feed goes through the same gate as the long feed."""

    def test_risk_off_offers_only_puts_from_the_weak_screen(self):
        rows = [{"symbol": "WEAK1", "mom20": -0.30, "rsi": 40.0},
                {"symbol": "WEAK2", "mom20": -0.10, "rsi": 45.0}]
        out = gated_candidates(None, 10, 73, set(), "auto",
                               rows=rows, force_side="put")
        self.assertEqual(out, [("WEAK1", "put"), ("WEAK2", "put")])

    def test_risk_off_still_applies_the_held_filter(self):
        # ruling 1: the short path must not lose "no doubling a name"
        rows = [{"symbol": "WEAK1", "mom20": -0.30, "rsi": 40.0}]
        out = gated_candidates(None, 10, 73, {"WEAK1"}, "auto",
                               rows=rows, force_side="put")
        self.assertEqual(out, [])

    def test_risk_off_still_applies_the_rsi_cap(self):
        rows = [{"symbol": "WEAK1", "mom20": -0.30, "rsi": 90.0}]
        out = gated_candidates(None, 10, 73, set(), "auto",
                               rows=rows, force_side="put")
        self.assertEqual(out, [])

    def test_weak_feed_still_truncates_to_top(self):
        rows = [{"symbol": f"W{i}", "mom20": -0.1 * i, "rsi": 40.0}
                for i in range(1, 8)]
        out = gated_candidates(None, 3, 73, set(), "auto",
                               rows=rows, force_side="put")
        self.assertEqual(out, [("W1", "put"), ("W2", "put"), ("W3", "put")])

    def test_risk_on_direction_rule_is_unchanged(self):
        rows = [{"symbol": "UP1", "mom20": 0.20, "rsi": 40.0},
                {"symbol": "DN1", "mom20": -0.20, "rsi": 40.0}]
        out = gated_candidates(None, 10, 73, set(), "auto", rows=rows)
        self.assertEqual(out, [("UP1", "call"), ("DN1", "put")])

    def test_unknown_regime_fails_closed_to_long_only(self):
        # UNKNOWN must not manufacture put candidates
        self.assertEqual(SCR._direction_for_row({"mom20": -0.9}), "put")
        self.assertEqual(SCR._direction_for_row({"mom20": -0.9}, "UNKNOWN"),
                         "put")
        # and a forced weak feed is only used in RISK_OFF, asserted at main() level


class DirectionRuleTest(unittest.TestCase):
    def test_direction_for_row_follows_momentum(self):
        self.assertEqual(SCR._direction_for_row({"mom20": 0.0}), "call")
        self.assertEqual(SCR._direction_for_row({"mom20": 0.25}), "call")
        self.assertEqual(SCR._direction_for_row({"mom20": -0.01}), "put")
        self.assertEqual(SCR._direction_for_row({}), "call")  # no mom -> long


class RegimeTest(unittest.TestCase):
    def test_regime_returns_unknown_when_spy_fetch_fails(self):
        with mock.patch.object(SCR, "_spy_closes", lambda: []):
            self.assertEqual(_regime(), "UNKNOWN")

    def test_regime_maps_risk_off(self):
        with mock.patch.object(SCR, "_spy_closes",
                               lambda: [100.0] * 50 + [50.0]):
            self.assertEqual(_regime(), "RISK_OFF")

    def test_regime_never_raises(self):
        def boom():
            raise RuntimeError("yfinance down")

        with mock.patch.object(SCR, "_spy_closes", boom):
            self.assertEqual(_regime(), "UNKNOWN")


class WeakRowsTest(unittest.TestCase):
    def test_weak_rows_returns_empty_on_a_missing_file(self):
        self.assertEqual(weak_rows("/nonexistent/weak.json"), [])

    def test_weak_rows_reads_the_tail(self):
        rows = [{"symbol": "WEAK1", "mom20": -0.3, "rsi": 40.0}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "weak.json"
            path.write_text(json.dumps({"date": "2026-11-05", "weak": rows}),
                            encoding="utf-8")
            self.assertEqual(weak_rows(path), rows)

    def test_weak_rows_without_a_tail_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "weak.json"
            path.write_text(json.dumps({"date": "2026-11-05"}),
                            encoding="utf-8")
            self.assertEqual(weak_rows(path), [])


class MainRoutingTest(unittest.TestCase):
    """Rulings 2 and 3, at the level where entries are actually chosen."""

    ARGV = ["--no-exits", "--no-notify"]

    def setUp(self):
        self.calls: list[dict] = []
        self.rows = [{"symbol": "WEAK1", "mom20": -0.30, "rsi": 40.0}]
        self.ranked = [{"symbol": "STRONG1", "rsi": 50.0, "mom20": 0.20}]

    def _screens(self, d):
        weak = Path(d) / "weak.json"
        weak.write_text(json.dumps({"date": "2026-11-05", "weak": self.rows}),
                        encoding="utf-8")
        screen = Path(d) / "latest.json"
        screen.write_text(json.dumps({"date": "2026-11-05",
                                      "ranked": self.ranked}),
                          encoding="utf-8")
        self.screen = str(screen)
        return screen, weak

    def _main(self, argv, *, regime, held):
        def spy_run(cmd):
            raise AssertionError(f"no subprocess expected: {cmd}")

        def spy_gated(screen_path, top, max_rsi, held_set, direction, **kw):
            self.calls.append({"screen": screen_path, "top": top,
                               "max_rsi": max_rsi, "held": held_set,
                               "direction": direction, **kw})
            return []

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            screen, weak = self._screens(d)
            argv = self.ARGV + ["--screen", str(screen),
                                "--weak-screen", str(weak)] + argv
            with mock.patch.object(SCR, "run", spy_run), \
                    mock.patch.object(SCR, "held_underliers",
                                      lambda env_file: held), \
                    mock.patch.object(SCR, "_regime", lambda: regime), \
                    mock.patch.object(SCR, "gated_candidates", spy_gated), \
                    redirect_stdout(out):
                rc = SCR.main(argv)
        return rc, out.getvalue()

    def test_risk_off_auto_uses_the_weak_feed_as_puts(self):
        rc, out = self._main([], regime="RISK_OFF", held=set())
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertIsNone(call["screen"])          # weak.json, not latest.json
        self.assertEqual(call["rows"], self.rows)
        self.assertEqual(call["force_side"], "put")
        self.assertEqual(call["direction"], "auto")
        self.assertIn("regime=RISK_OFF", out)
        self.assertIn("feed=weak", out)

    def test_risk_on_keeps_the_ranked_feed(self):
        rc, out = self._main([], regime="RISK_ON", held=set())
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual(call["screen"], self.screen)
        self.assertNotIn("rows", call)
        self.assertNotIn("force_side", call)
        self.assertIn("regime=RISK_ON", out)
        self.assertIn("feed=ranked", out)

    def test_unknown_regime_keeps_the_ranked_feed(self):
        # missing evidence is not permission to trade
        rc, out = self._main([], regime="UNKNOWN", held=set())
        self.assertEqual(rc, 0)
        call = self.calls[0]
        self.assertEqual(call["screen"], self.screen)
        self.assertNotIn("rows", call)
        self.assertIn("regime=UNKNOWN", out)
        self.assertIn("feed=ranked", out)

    def test_explicit_direction_overrides_the_regime_default(self):
        rc, _ = self._main(["--direction", "call"], regime="RISK_OFF",
                           held=set())
        self.assertEqual(rc, 0)
        call = self.calls[0]
        self.assertEqual(call["screen"], self.screen)
        self.assertNotIn("force_side", call)
        self.assertEqual(call["direction"], "call")

    def test_held_none_blocks_entries_even_in_risk_off(self):
        # ruling 2 + the fail-closed holdings gate: unreadable broker state
        # produces no candidates, weak feed or not.
        rc, out = self._main([], regime="RISK_OFF", held=None)
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [])           # gated_candidates never ran
        self.assertIn('"placed": null', out)
        self.assertNotIn("candidates=", out)


if __name__ == "__main__":
    unittest.main()
