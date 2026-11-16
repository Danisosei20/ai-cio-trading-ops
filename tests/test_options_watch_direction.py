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
from datetime import datetime
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
_pilot_action = SCR._pilot_action


class PilotActionParseTest(unittest.TestCase):
    """The watch must read the pilot's verdict, whatever shape it arrives in.

    2026-11-16: the pilot prints `json.dumps(rec, indent=2)` (multi-line),
    and a yfinance/pandas deprecation line lands on stdout before it. The
    old line-by-line parser reported "no-json" for 13 confirmed placements,
    so the log claimed the pilot said nothing while the broker held the
    orders - and every window/premium refusal reason was invisible too.
    """

    def test_multiline_indented_json_is_read(self):
        out = ("INTC spot 56.27 (alpaca-iex 2026-11-16T14:23:11Z)\n"
               "{\n"
               '  "ticker": "INTC",\n'
               '  "action": "buy_placed",\n'
               '  "contract": "INTC261218P00050000",\n'
               '  "qty": 2\n'
               "}\n")
        self.assertEqual(_pilot_action(out), "buy_placed")

    def test_warning_line_before_the_json_is_skipped(self):
        out = ("<stdin>:1: FutureWarning: ...\n"
               "df.rename(columns={'Surprise (%)': 'Surprise(%)'}, "
               "inplace=True)\n"
               "{\n"
               '  "ticker": "MRVL",\n'
               '  "action": "skip",\n'
               '  "reason": "premium ~$3000 exceeds $3000 cap"\n'
               "}\n")
        self.assertEqual(_pilot_action(out), "skip")

    def test_single_line_json_still_works(self):
        out = '{"ticker": "TER", "action": "dry_run_buy", "qty": 1}\n'
        self.assertEqual(_pilot_action(out), "dry_run_buy")

    def test_empty_output_is_no_json(self):
        self.assertEqual(_pilot_action(""), "no-json")
        self.assertEqual(_pilot_action("no output at all"), "no-json")


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
        self.pending: set = set()

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

    _UNSET = object()

    def _main(self, argv, *, regime, held, rows=None, ranked=None,
              pending=_UNSET):
        def spy_run(cmd):
            # A pilot that always dry-runs: the loop breaks on the first
            # candidate, so the test observes the choice, not an order.
            return 0, '{"action": "dry_run_buy", "ticker": "stub"}', ""

        def spy_analyze(ticker, **kw):
            return {"verdict": {"verdict": "neutral", "reasons": ["stub"]}}

        def spy_gated(screen_path, top, max_rsi, held_set, direction, **kw):
            """Faithful stand-in: applies the same held/RSI/top filtering the
            real `gated_candidates` does, so tests can observe which
            candidates a feed actually yields."""
            self.calls.append({"screen": screen_path, "top": top,
                               "max_rsi": max_rsi, "held": held_set,
                               "direction": direction, **kw})
            rows = kw.get("rows")
            if rows is None:
                with open(screen_path, encoding="utf-8") as fh:
                    rows = json.load(fh).get("ranked") or []
            picked: list[tuple[str, str]] = []
            for row in rows:
                symbol = str(row.get("symbol") or "").strip().upper()
                symbol = symbol.replace(".", "-")
                if not symbol or symbol in held_set:
                    continue
                rsi = row.get("rsi")
                if rsi is not None and float(rsi) > max_rsi:
                    continue
                if kw.get("force_side"):
                    side = kw["force_side"]
                elif direction != "auto":
                    side = direction
                else:
                    side = "call" if float(row.get("mom20") or 0) >= 0 else "put"
                picked.append((symbol, side))
                if len(picked) >= top:
                    break
            return picked

        if rows is not None:
            self.rows = rows
        if ranked is not None:
            self.ranked = ranked
        if pending is self._UNSET:
            pending = self.pending
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            screen, weak = self._screens(d)
            argv = self.ARGV + ["--screen", str(screen),
                                "--weak-screen", str(weak)] + argv
            with mock.patch.object(SCR, "run", spy_run), \
                    mock.patch.object(SCR, "held_underliers",
                                      lambda env_file: held), \
                    mock.patch.object(SCR, "open_order_underliers",
                                      lambda env_file: pending), \
                    mock.patch.object(SCR, "_regime", lambda: regime), \
                    mock.patch.object(SCR, "gated_candidates", spy_gated), \
                    mock.patch.object(SCR, "liquid_signal_filter",
                                      lambda *a, **k: (True, "stub ok")), \
                    mock.patch("scripts.zone_analysis.analyze", spy_analyze), \
                    redirect_stdout(out):
                rc = SCR.main(argv)
        return rc, out.getvalue()

    def test_risk_off_auto_offers_both_feeds(self):
        # A down tape gets the weak tail as puts AND the ranked top by its own
        # momentum, because a regime that only ever offers one direction
        # filters out names the tape happens to be sitting in.
        rc, out = self._main([], regime="RISK_OFF", held=set())
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls), 2)
        puts, calls = self.calls
        self.assertIsNone(puts["screen"])          # weak.json, not latest.json
        self.assertEqual(puts["rows"], self.rows)
        self.assertEqual(puts["force_side"], "put")
        self.assertEqual(puts["direction"], "auto")
        self.assertEqual(calls["screen"], self.screen)
        self.assertNotIn("force_side", calls)
        self.assertIn("regime=RISK_OFF", out)
        self.assertIn("feed=weak+ranked", out)

    def test_risk_off_does_not_duplicate_a_name_in_both_feeds(self):
        rc, out = self._main([], regime="RISK_OFF", held=set(),
                             rows=[{"symbol": "BOTH", "mom20": -0.2,
                                    "rsi": 40.0, "price": 10.0}],
                             ranked=[{"symbol": "BOTH", "mom20": 0.2,
                                      "rsi": 40.0, "price": 10.0}])
        self.assertEqual(rc, 0)
        # The put (weak feed, first) wins; the ranked copy is not added twice
        self.assertIn("[('BOTH', 'put')]", out)
        self.assertNotIn("('BOTH', 'call')", out)

    def test_loop_runs_a_cycle_per_interval_then_stops(self):
        # The loop must repeat the full cycle, not just sleep once.
        import scripts.options_watch_trade as owt

        cycles_seen: list = []
        sleeps: list = []
        real_cycle, real_sleep = owt._run_cycle, owt.time.sleep
        class FakeDatetime(datetime):
            """Clock that walks 14:00, 14:00, ... then 16:00 (past --until)."""
            ticks = {"n": 0}

            @classmethod
            def now(cls, tz=None):
                cls.ticks["n"] += 1
                hour = 16 if cls.ticks["n"] >= 5 else 14
                return cls(2026, 11, 6, hour, 0, 0, tzinfo=tz)

        def fake_cycle(args, py):
            cycles_seen.append(len(cycles_seen) + 1)
            return None

        try:
            owt._run_cycle = fake_cycle
            owt.time.sleep = lambda s: sleeps.append(s)
            out = io.StringIO()
            with mock.patch.object(owt, "datetime", FakeDatetime), \
                    redirect_stdout(out):
                rc = owt.main(["--no-exits", "--no-notify", "--loop", "30",
                               "--until", "15:45"])
        finally:
            owt._run_cycle, owt.time.sleep = real_cycle, real_sleep
        text = out.getvalue()
        self.assertEqual(rc, 0)
        # Two cycles inside the window, then the clock passes --until and it stops
        self.assertEqual(cycles_seen, [1, 2])
        # Two full intervals, each capped at min(interval, time-to-stop)
        self.assertEqual(sleeps, [1800, 1800])
        self.assertIn("[loop] cycle 1", text)
        self.assertIn("[loop] cycle 2", text)
        self.assertIn("[loop] done, 2 cycle(s)", text)

    def test_loop_never_sleeps_past_its_stop_time(self):
        # A cycle that finishes 60s before --until must wake exactly at the
        # stop time, not a full interval later - otherwise the loop lingers
        # one interval past the time it promised to end.
        import scripts.options_watch_trade as owt

        sleeps: list = []
        cycles_seen: list = []
        real_cycle, real_sleep = owt._run_cycle, owt.time.sleep

        class FakeDatetime(datetime):
            ticks = {"n": 0}

            @classmethod
            def now(cls, tz=None):
                cls.ticks["n"] += 1
                # 15:44, then 15:44, then 16:00 (past --until 15:45)
                hour, minute = (16, 0) if cls.ticks["n"] >= 3 else (15, 44)
                return cls(2026, 11, 6, hour, minute, 0, tzinfo=tz)

        try:
            owt._run_cycle = lambda a, p: cycles_seen.append(1)
            owt.time.sleep = lambda s: sleeps.append(s)
            out = io.StringIO()
            with mock.patch.object(owt, "datetime", FakeDatetime), \
                    redirect_stdout(out):
                owt.main(["--no-exits", "--no-notify", "--loop", "30",
                          "--until", "15:45"])
        finally:
            owt._run_cycle, owt.time.sleep = real_cycle, real_sleep
        self.assertEqual(cycles_seen, [1])
        # 30 minutes would be 1800s; only 60s remain before 15:45
        self.assertEqual(sleeps, [60])

    def test_no_loop_flag_means_a_single_run(self):
        rc, out = self._main([], regime="RISK_ON", held=set())
        self.assertEqual(rc, 0)
        self.assertNotIn("[loop]", out)

    def test_pilot_action_finds_json_after_stdout_noise(self):
        # yfinance prints deprecation warnings to stdout during the quote
        # fetch, so the pilot's JSON is rarely on a clean line. Requiring the
        # line to start with "{" made this report "no-json" for a purchase
        # the pilot had confirmed.
        from scripts.options_watch_trade import _pilot_action

        noisy = ("C:\\...\\utils.py:568: Pandas4Warning: ...\n"
                 "  quotes.sort_index(inplace=True)\n"
                 "See https://pandas.pydata.org/docs/...\n"
                 '{"ticker": "INTC", "action": "buy_placed", "qty": 5}\n')
        self.assertEqual(_pilot_action(noisy), "buy_placed")

    def test_pilot_action_reports_no_json_when_there_is_none(self):
        from scripts.options_watch_trade import _pilot_action
        self.assertEqual(_pilot_action("spot 56.0 (yfinance-1m)"), "no-json")
        self.assertEqual(_pilot_action(""), "no-json")

    def test_loop_notifies_only_when_a_cycle_placed_something(self):
        # A cycle that places nothing must not Slack the operator.
        notified: list = []

        def spy_run(cmd):
            notified.append(cmd)
            return 0, '{"action": "dry_run_buy"}', ""

        with tempfile.TemporaryDirectory() as d:
            screen, weak = self._screens(d)
            argv = self.ARGV + ["--screen", str(screen),
                                "--weak-screen", str(weak)]
            with mock.patch.object(SCR, "run", spy_run), \
                    mock.patch.object(SCR, "held_underliers",
                                      lambda env_file: set()), \
                    mock.patch.object(SCR, "_regime", lambda: "RISK_ON"), \
                    mock.patch.object(SCR, "gated_candidates",
                                      lambda *a, **k: []), \
                    mock.patch.object(SCR, "liquid_signal_filter",
                                      lambda *a, **k: (True, "ok")), \
                    redirect_stdout(io.StringIO()):
                SCR.main(argv)
        # No candidates => nothing placed => no watchlist send
        self.assertEqual(notified, [])

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

    def test_a_name_with_an_unfilled_order_is_out_of_reach(self):
        # The pilot's --max-positions reads positions, and an unfilled order is
        # not a position - so without this the loop stacked one stale limit per
        # cycle until the close.
        rc, out = self._main([], regime="RISK_OFF", held=set(),
                             rows=[{"symbol": "WEAK1", "mom20": -0.30,
                                    "rsi": 40.0}], pending={"WEAK1"})
        self.assertEqual(rc, 0)
        self.assertIn("pending=['WEAK1']", out)
        self.assertNotIn("('WEAK1'", out)

    def test_pending_orders_do_not_block_other_names(self):
        rc, out = self._main(
            [], regime="RISK_OFF", held=set(),
            rows=[{"symbol": "WEAK1", "mom20": -0.30, "rsi": 40.0},
                  {"symbol": "WEAK2", "mom20": -0.10, "rsi": 45.0}],
            pending={"WEAK1"})
        self.assertEqual(rc, 0)
        self.assertIn("('WEAK2', 'put')", out)

    def test_unreadable_open_orders_blocks_entries(self):
        # Missing evidence is not permission to trade, same rule as holdings.
        rc, out = self._main([], regime="RISK_OFF", held=set(),
                             pending=None)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls), 0)
        self.assertNotIn("candidates=", out)

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
