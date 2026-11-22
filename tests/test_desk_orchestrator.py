import unittest

from scripts.desk_stage_agents import StageReport
from scripts.desk_orchestrator import decide


def rep(agent, status="ok"):
    return StageReport(agent=agent, role="", status=status, summary="", evidence=[],
                       metrics={}, started_at="", finished_at="", duration_seconds=0,
                       exit_code=0, stderr_tail="")


OK = {n: rep(n) for n in ("Certifier", "Reconciler", "ExitGuard", "Screener",
                          "ZoneAnalyst", "ResearchFleet", "BarReader")}
SAFE = {"TRADING_ENABLED": "false", "PAPER_TRADING_ENABLED": "true",
        "TRADING_MODE": "paper_auto"}
SESSION = {"tradeable": True, "market_clock": "open", "window_open": True}


class DecideTests(unittest.TestCase):
    def test_all_ok_is_proceed(self):
        d = decide(list(OK.values()), safety=SAFE, session=SESSION,
                   now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(d.verdict, "proceed")

    def test_live_flag_halts_even_when_agents_ok(self):
        d = decide(list(OK.values()), safety={**SAFE, "TRADING_ENABLED": "true"},
                   session=SESSION, now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(d.verdict, "halt")

    def test_closed_session_halts(self):
        d = decide(list(OK.values()), safety=SAFE,
                   session={**SESSION, "tradeable": False},
                   now_iso="2026-11-21T09:45:00-05:00")
        self.assertEqual(d.verdict, "halt")

    def test_missing_artifact_halts(self):
        reports = [r for r in OK.values() if r.agent != "Certifier"]
        d = decide(reports, safety=SAFE, session=SESSION,
                   now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(d.verdict, "halt")

    def test_warn_is_review_not_proceed(self):
        reports = [rep(n, "warn" if n == "ZoneAnalyst" else "ok") for n in OK]
        d = decide(reports, safety=SAFE, session=SESSION,
                   now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(d.verdict, "review")

    def test_stale_required_artifact_is_review(self):
        reports = [rep(n, "warn" if n == "BarReader" else "ok") for n in OK]
        d = decide(reports, safety=SAFE, session=SESSION,
                   now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(d.verdict, "review")


NOW = "2026-11-21T10:20:00-05:00"
ET_DATE = "2026-11-21"


def warns(*names):
    """One warn report per named agent, ok for the rest of the required set."""
    return [rep(n, "warn" if n in names else "ok") for n in OK]


def ov(**changes):
    """A valid override for the canonical warn agent, with optional changes."""
    override = {"agent": "ZoneAnalyst", "date": ET_DATE, "reason": "owner approved"}
    override.update(changes)
    return override


class OverrideTests(unittest.TestCase):
    def decide(self, reports, overrides=None, safety=SAFE):
        return decide(reports, safety=safety, session=SESSION,
                      now_iso=NOW, overrides=overrides)

    def test_valid_override_flips_review_to_proceed_and_is_recorded(self):
        override = ov()
        d = self.decide(warns("ZoneAnalyst"), [override])
        self.assertEqual(d.verdict, "proceed")
        self.assertEqual(d.overrides, [override])

    def test_override_cannot_clear_halt(self):
        d = self.decide(warns("ZoneAnalyst"), [ov()],
                        safety={**SAFE, "TRADING_ENABLED": "true"})
        self.assertEqual(d.verdict, "halt")
        self.assertEqual(d.overrides, [])

    def test_wrong_date_is_ignored(self):
        d = self.decide(warns("ZoneAnalyst"), [ov(date="2026-11-20")])
        self.assertEqual(d.verdict, "review")
        self.assertEqual(d.overrides, [])

    def test_empty_reason_is_ignored(self):
        d = self.decide(warns("ZoneAnalyst"), [ov(reason="   ")])
        self.assertEqual(d.verdict, "review")
        self.assertEqual(d.overrides, [])

    def test_non_required_agent_is_ignored(self):
        d = self.decide(warns("ZoneAnalyst"), [ov(agent="Reporter")])
        self.assertEqual(d.verdict, "review")
        self.assertEqual(d.overrides, [])

    def test_unknown_agent_is_ignored(self):
        d = self.decide(warns("ZoneAnalyst"), [ov(agent="NotAnAgent")])
        self.assertEqual(d.verdict, "review")
        self.assertEqual(d.overrides, [])

    def test_ok_required_agent_is_ignored(self):
        d = self.decide(warns("ZoneAnalyst"), [ov(agent="Certifier")])
        self.assertEqual(d.verdict, "review")
        self.assertEqual(d.overrides, [])

    def test_non_dict_override_is_ignored(self):
        d = self.decide(warns("ZoneAnalyst"), ["owner said go", None, 42])
        self.assertEqual(d.verdict, "review")
        self.assertEqual(d.overrides, [])

    def test_partial_coverage_of_two_warns_stays_review(self):
        d = self.decide(warns("ZoneAnalyst", "BarReader"), [ov()])
        self.assertEqual(d.verdict, "review")
        self.assertEqual(d.overrides, [])

    def test_all_warns_covered_flips_to_proceed(self):
        overrides = [ov(), ov(agent="BarReader", reason="ok")]
        d = self.decide(warns("ZoneAnalyst", "BarReader"), overrides)
        self.assertEqual(d.verdict, "proceed")
        self.assertEqual(len(d.overrides), 2)


class DuplicateReportTests(unittest.TestCase):
    def test_fail_followed_by_ok_halts(self):
        reports = [r for r in OK.values() if r.agent != "Certifier"]
        reports += [rep("Certifier", "fail"), rep("Certifier", "ok")]
        d = decide(reports, safety=SAFE, session=SESSION, now_iso=NOW)
        self.assertEqual(d.verdict, "halt")
        self.assertIn("Certifier: duplicate required report", d.reasons)

    def test_duplicate_ok_reports_halt(self):
        d = decide(list(OK.values()) + [rep("Certifier", "ok")],
                   safety=SAFE, session=SESSION, now_iso=NOW)
        self.assertEqual(d.verdict, "halt")
        self.assertIn("Certifier: duplicate required report", d.reasons)


if __name__ == "__main__":
    unittest.main()
