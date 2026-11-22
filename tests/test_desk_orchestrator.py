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


if __name__ == "__main__":
    unittest.main()
