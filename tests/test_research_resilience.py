"""Research-path resilience: the NIM tool-call 400 and its exact-observed cases.

2026-11-06 live defects, both in the research (judge) path:

  - QQQ / TSLA (2026-11-02), HII (10-29), PTC (10-28) all died with
    ``ValueError: {'message': 'unexpected tokens remaining in message header:
    Some("to=functions.get_indicators")', 'type': 'BadRequestError',
    'code': 400}`` -- NIM's function-call parser rejected the model's own
    tool-call emission and the ticker recorded NO decision. One bounded
    retry keeps the evidence path; a second failure still fails closed.

  - The 15:10 watch's Slack close message flagged contracts "over cap"
    against the watchlist's own $1200 default while the watch enforced
    $3000, so the report disagreed with the loop that produced it. The
    notify call must carry the watch's cap.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from scripts import tradingagents_paper_auto as auto

ROOT = Path(__file__).resolve().parents[1]
WATCH = ROOT / "scripts" / "options_watch_trade.py"
WATCHLIST = ROOT / "scripts" / "options_watchlist.py"
BAT = ROOT / "scripts" / "auto_trade_weekday.bat"


class NimToolcallParseErrorTest(unittest.TestCase):
    """The exact live strings must be recognised; unrelated errors must not."""

    def test_observed_live_signatures_match(self):
        live = [
            'ValueError: {\'message\': \'unexpected tokens remaining in message '
            'header: Some("to=functions.get_indicators")\', \'type\': '
            '\'BadRequestError\', \'param\': None, \'code\': 400}',
            'ValueError: {\'message\': \'unexpected tokens remaining in message '
            'header: Some("to=functions.get_macro_indicators")\', \'type\': '
            '\'BadRequestError\', \'param\': None, \'code\': 400}',
            'ValueError: {\'message\': \'unexpected tokens remaining in message '
            'header: Some("to=")\', \'type\': \'BadRequestError\', '
            '\'param\': None, \'code\': 400}',
            'ValueError: {\'message\': \'unexpected tokens remaining in message '
            'header: Some("to=... The user presented QQQ and wants a report. '
            'We have news.")\', \'type\': \'BadRequestError\', \'param\': None, '
            '\'code\': 400}',
        ]
        for text in live:
            with self.subTest(text=text[:60]):
                self.assertTrue(auto._is_nim_toolcall_parse_error(ValueError(text)))

    def test_unrelated_errors_do_not_match(self):
        # A timeout, a policy gate, or an ordinary 400 must NOT be retried:
        # the retry is specific to NIM's tool-call parse failure.
        for exc in (TimeoutError("research exceeded 1200s for TSLA"),
                    ValueError("no such analyst: bogus"),
                    RuntimeError("connection reset by peer"),
                    ValueError("{'message': 'model not found', 'code': 404}")):
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(auto._is_nim_toolcall_parse_error(exc))

    def test_retry_is_bounded_to_one_attempt(self):
        """Source guard: the tool-call retry fires once, never loops.

        Three propagate calls exist in run_research: the original, the single
        NIM-parse-error retry, and the pre-existing deep-tier fallback. A
        fourth would mean the retry can feed itself.
        """
        tree = ast.parse(auto_path_text())
        propagates = 0
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "propagate"):
                propagates += 1
        self.assertEqual(propagates, 3)


class NotifyCapConsistencyTest(unittest.TestCase):
    """The close message must report against the cap the desk enforces."""

    def test_watch_notify_passes_its_premium_cap(self):
        src = WATCH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        notify_calls = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "run"):
                for arg in node.args:
                    if isinstance(arg, ast.List):
                        strs = [e.value for e in arg.elts
                                if isinstance(e, ast.Constant)
                                and isinstance(e.value, str)]
                        if "scripts/options_watchlist.py" in strs:
                            notify_calls.append(strs)
        self.assertTrue(notify_calls,
                        "the watch must call options_watchlist.py to notify")
        for args in notify_calls:
            self.assertIn("--premium-cap", args,
                          "notify must carry the watch's enforced premium cap")

    def _flag_defaults(self, path: Path) -> dict:
        """{'--premium-cap': <default value>} from an argparse.add_argument."""
        out = {}
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                flags = [a.value for a in node.args
                         if isinstance(a, ast.Constant)
                         and isinstance(a.value, str)]
                for kw in node.keywords:
                    if kw.arg == "default":
                        node_v = kw.value
                        if isinstance(node_v, ast.Constant):
                            value = node_v.value
                        elif (isinstance(node_v, ast.Call)
                              and node_v.args
                              and isinstance(node_v.args[0], ast.Constant)):
                            value = node_v.args[0].value  # Decimal("3000")
                        else:
                            continue
                        for flag in flags:
                            out[flag] = value
        return out

    def test_watchlist_default_matches_the_watch_default(self):
        watchlist_defaults = self._flag_defaults(WATCHLIST)
        watch_defaults = self._flag_defaults(WATCH)
        self.assertIn("--premium-cap", watchlist_defaults)
        self.assertIn("--premium-cap", watch_defaults)
        self.assertEqual(watchlist_defaults["--premium-cap"],
                         watch_defaults["--premium-cap"],
                         "the reported cap must equal the enforced cap")

    def test_watchlist_over_cap_uses_the_effective_cap(self):
        src = WATCHLIST.read_text(encoding="utf-8")
        self.assertIn('"over_cap": premium > args.premium_cap', src,
                      "the over-cap flag must compare against the cap in use")


class ScheduledBudgetTest(unittest.TestCase):
    """The scheduled research run states its per-ticker budget explicitly."""

    def test_auto_trade_weekday_passes_ticker_timeout(self):
        src = BAT.read_text(encoding="utf-8", errors="replace")
        research_line = next(
            (ln for ln in src.splitlines()
             if "tradingagents_paper_auto.py" in ln and "--screen" in ln), "")
        self.assertTrue(research_line, "scheduled research invocation not found")
        self.assertIn("--ticker-timeout 1200", research_line,
                      "the scheduled run must spend the measured budget, "
                      "not inherit a default that changed under it")


def auto_path_text() -> str:
    return (ROOT / "scripts" / "tradingagents_paper_auto.py").read_text(
        encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
