import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.desk_stage_agents import StageReport
from scripts.desk_orchestrator import decide, prove, read_latest, record_override, run_slot, write_run


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


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def test_run_is_immutable_and_latest_points_to_it(self):
        d = decide(list(OK.values()), safety=SAFE, session=SESSION,
                   now_iso="2026-11-21T10:20:00-05:00")
        p = write_run(self.root, "20261121-0945", list(OK.values()), d)
        self.assertEqual(p.name, "decision.json")
        self.assertEqual(read_latest(self.root)["run_id"], "20261121-0945")
        with self.assertRaises(FileExistsError):
            write_run(self.root, "20261121-0945", list(OK.values()), d)

    def test_prove_lists_every_agent(self):
        d = decide(list(OK.values()), safety=SAFE, session=SESSION,
                   now_iso="2026-11-21T10:20:00-05:00")
        write_run(self.root, "r1", list(OK.values()), d)
        text = prove(self.root, "r1")
        for name in ("Certifier", "Reconciler", "ExitGuard", "Screener", "ZoneAnalyst"):
            self.assertIn(name, text)

    def test_override_cannot_clear_safety_halt(self):
        d = decide(list(OK.values()), safety={**SAFE, "TRADING_ENABLED": "true"},
                   session=SESSION, now_iso="2026-11-21T10:20:00-05:00")
        write_run(self.root, "r2", list(OK.values()), d)
        self.assertFalse(record_override(self.root, run_id="r2", agent="Certifier",
                                         reason="owner said go", now_iso="2026-11-21T10:30:00-05:00"))


class RunnerTests(unittest.TestCase):
    def test_0945_slot_never_runs_order_agent(self):
        called = []
        class P:
            returncode = 0
            stdout = ""
            stderr = ""
        def fake(cmd, **kw):
            called.append(cmd)
            return P()
        run_slot(Path(tempfile.mkdtemp()), "r0945", slot="0945", exec_cmd=fake)
        self.assertFalse(any("tradingagents_paper_auto.py" in " ".join(c)
                             and "--dry-run" not in c for c in called))

    def test_1020_slot_runs_order_agent_after_proceed(self):
        called = []
        class P:
            returncode = 0
            stdout = ""
            stderr = ""
        def fake(cmd, **kw):
            called.append(cmd)
            return P()
        root = Path(tempfile.mkdtemp())
        (root / ".env").write_text(
            "TRADING_ENABLED=false\nPAPER_TRADING_ENABLED=true\nTRADING_MODE=paper_auto\n",
            encoding="utf-8")
        # Seed every required artifact so normalization yields ok and decide proceeds.
        for p in ("outputs/paper/fill_reconciliation.json",
                  "outputs/paper/options_guard.json",
                  "outputs/certification/paper_certification.json",
                  "outputs/screener/latest.json",
                  "outputs/orchestrator/r1020/zone.json",
                  "outputs/orchestrator/r1020/research.json",
                  "outputs/orchestrator/r1020/bars_NVDA.json"):
            f = root / p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text('{"status": "ok", "verdict": "PASS"}', encoding="utf-8")
        # The real session depends on the wall clock and broker clock; pin it so
        # the test proves the decision gate rather than today's date.
        with mock.patch("scripts.agent_office.trading_window", return_value=SESSION):
            d = run_slot(root, "r1020", slot="1020", exec_cmd=fake)
        self.assertEqual(d.verdict, "proceed")
        self.assertTrue(any("tradingagents_paper_auto.py" in " ".join(c)
                            and "--dry-run" not in c for c in called))

    def test_failed_stage_still_writes_decision(self):
        class P:
            returncode = 1
            stdout = ""
            stderr = "boom"
        root = Path(tempfile.mkdtemp())
        d = run_slot(root, "r0945fail", slot="0945", exec_cmd=lambda *a, **k: P())
        self.assertEqual(d.verdict, "halt")
        run_dir = root / "outputs/orchestrator/r0945fail"
        self.assertTrue((run_dir / "decision.json").exists())
        self.assertEqual(d.run_id, "r0945fail")
        # write_run is the only writer: no duplicate reports at <run_id>/<agent>.json.
        self.assertFalse((run_dir / "Reconciler.json").exists())


if __name__ == "__main__":
    unittest.main()
