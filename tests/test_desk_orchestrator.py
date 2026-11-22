import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import desk_orchestrator as orch
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

    def test_batch_calls_orchestrator_and_has_no_inline_order_command(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "auto_trade_weekday.bat").read_text(
            encoding="utf-8", errors="replace")
        self.assertIn("desk_orchestrator.py --slot 0945", text)
        for line in text.splitlines():
            command = line.strip()
            if command and not command.upper().startswith("REM"):
                self.assertNotIn("tradingagents_paper_auto.py", command,
                                 "the 09:45 batch must not place orders inline")


FIX_NOW = "2026-11-21T10:20:00-05:00"  # ET date 2026-11-21
SAFE_ENV = ("TRADING_ENABLED=false\nPAPER_TRADING_ENABLED=true\n"
            "TRADING_MODE=paper_auto\n")
SEED_PATHS = ("outputs/paper/fill_reconciliation.json",
              "outputs/paper/options_guard.json",
              "outputs/certification/paper_certification.json",
              "outputs/screener/latest.json",
              "outputs/orchestrator/{run_id}/zone.json",
              "outputs/orchestrator/{run_id}/research.json",
              "outputs/orchestrator/{run_id}/bars_NVDA.json")


class Proc:
    returncode = 0
    stdout = ""
    stderr = ""


def seed_required(root, run_id, *, stale_screener=False):
    """Fresh required artifacts; optionally age the screener past 24h."""
    for template in SEED_PATHS:
        path = root / template.format(run_id=run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = "2026-11-20T08:00:00-05:00" if stale_screener and "screener" in str(path) \
            else "2026-11-21T10:19:00-05:00"
        path.write_text(json.dumps({"status": "ok", "verdict": "PASS", "generated_at": stamp}),
                        encoding="utf-8")


def safe_root(root):
    (root / ".env").write_text(SAFE_ENV, encoding="utf-8")


class OverrideEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        safe_root(self.root)

    def tearDown(self):
        self.t.cleanup()

    def test_recorded_override_makes_next_run_slot_proceed(self):
        seed_required(self.root, "r_review", stale_screener=True)
        with mock.patch("scripts.agent_office.trading_window", return_value=SESSION):
            first = run_slot(self.root, "r_review", slot="0945",
                             exec_cmd=lambda *a, **k: Proc(), now_iso=FIX_NOW)
        self.assertEqual(first.verdict, "review", "stale screener must force review")

        self.assertTrue(record_override(self.root, run_id="r_review", agent="Screener",
                                        reason="owner approved stale screener",
                                        now_iso="2026-11-21T09:50:00-05:00"))

        seed_required(self.root, "r_next", stale_screener=True)
        with mock.patch("scripts.agent_office.trading_window", return_value=SESSION):
            second = run_slot(self.root, "r_next", slot="1020",
                              exec_cmd=lambda *a, **k: Proc(), now_iso=FIX_NOW)
        self.assertEqual(second.verdict, "proceed",
                         "today's recorded override must flip the next run to proceed")
        self.assertEqual(second.overrides, [
            {"agent": "Screener", "date": "2026-11-21", "reason": "owner approved stale screener"}])

    def test_loader_maps_only_todays_entries(self):
        run_dir = self.root / "outputs/orchestrator/r_audit"
        run_dir.mkdir(parents=True)
        entries = [
            {"run_id": "r_audit", "agent": "ZoneAnalyst", "reason": "ok",
             "now_iso": "2026-11-21T09:50:00-05:00"},
            {"run_id": "r_audit", "agent": "BarReader", "reason": "yesterday",
             "now_iso": "2026-11-20T09:50:00-05:00"},
            {"run_id": "r_audit", "agent": "Screener", "reason": "   ",
             "now_iso": "2026-11-21T09:51:00-05:00"},
            {"run_id": "r_audit", "agent": "Certifier", "now_iso": "2026-11-21T09:52:00-05:00"},
        ]
        (run_dir / "overrides.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n{bad json\n", encoding="utf-8")
        loaded = orch.load_overrides(self.root, now_iso=FIX_NOW)
        self.assertEqual(loaded, [{"agent": "ZoneAnalyst", "date": "2026-11-21", "reason": "ok"}])

    def test_override_cannot_flip_halt_in_next_run(self):
        # Recording on a review run does not authorize clearing a live-flag halt.
        seed_required(self.root, "r_review", stale_screener=True)
        with mock.patch("scripts.agent_office.trading_window", return_value=SESSION):
            run_slot(self.root, "r_review", slot="0945",
                     exec_cmd=lambda *a, **k: Proc(), now_iso=FIX_NOW)
        self.assertTrue(record_override(self.root, run_id="r_review", agent="Screener",
                                        reason="owner approved", now_iso="2026-11-21T09:50:00-05:00"))
        (self.root / ".env").write_text(SAFE_ENV + "TRADING_ENABLED=true\n", encoding="utf-8")
        seed_required(self.root, "r_live", stale_screener=True)
        with mock.patch("scripts.agent_office.trading_window", return_value=SESSION):
            halted = run_slot(self.root, "r_live", slot="1020",
                              exec_cmd=lambda *a, **k: Proc(), now_iso=FIX_NOW)
        self.assertEqual(halted.verdict, "halt")
        self.assertEqual(halted.overrides, [])


class FreshnessEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        safe_root(self.root)

    def tearDown(self):
        self.t.cleanup()

    def test_aged_required_artifact_yields_review_not_proceed(self):
        seed_required(self.root, "r_stale", stale_screener=True)
        with mock.patch("scripts.agent_office.trading_window", return_value=SESSION):
            d = run_slot(self.root, "r_stale", slot="0945",
                         exec_cmd=lambda *a, **k: Proc(), now_iso=FIX_NOW)
        self.assertEqual(d.verdict, "review")
        screener = next(r for r in d.reports if r.agent == "Screener")
        self.assertEqual(screener.status, "warn")
        # No order stage may fire on a review verdict, even at the 1020 slot.
        seed_required(self.root, "r_stale1020", stale_screener=True)
        with mock.patch("scripts.agent_office.trading_window", return_value=SESSION):
            d2 = run_slot(self.root, "r_stale1020", slot="1020",
                          exec_cmd=lambda *a, **k: Proc(), now_iso=FIX_NOW)
        self.assertEqual(d2.verdict, "review")
        order = next(r for r in d2.reports if r.agent == "OrderAgent")
        self.assertEqual(order.status, "skipped")


class OrderAgentProofTests(unittest.TestCase):
    def test_order_agent_is_in_decision_json_and_proof_matrix(self):
        root = Path(tempfile.mkdtemp())
        safe_root(root)
        seed_required(root, "r_proof")
        order_artifact = root / "outputs/paper/tradingagents/mock/auto.json"
        order_artifact.parent.mkdir(parents=True, exist_ok=True)
        order_artifact.write_text(
            json.dumps({"status": "ok", "generated_at": "2026-11-21T10:19:00-05:00"}),
            encoding="utf-8")
        with mock.patch("scripts.agent_office.trading_window", return_value=SESSION):
            d = run_slot(root, "r_proof", slot="1020",
                         exec_cmd=lambda *a, **k: Proc(), now_iso=FIX_NOW)
        self.assertEqual(d.verdict, "proceed")
        doc = json.loads((root / "outputs/orchestrator/r_proof/decision.json").read_text(
            encoding="utf-8"))
        self.assertIn("OrderAgent", [r["agent"] for r in doc["reports"]])
        matrix = prove(root, "r_proof")
        self.assertIn("OrderAgent | ok |", matrix)
        self.assertNotIn("OrderAgent | missing", matrix)


if __name__ == "__main__":
    unittest.main()
