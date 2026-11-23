"""Preflight must reflect the actual host, not macOS launchd on Windows."""
import subprocess
import unittest
from unittest import mock

from scripts import morning_check


class Proc:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.returncode = 0


class MorningCheckWindowsTests(unittest.TestCase):
    def test_schedules_uses_schtasks_on_windows(self):
        out = Proc("\n".join(["AI-CIO-AutoTrade-0945", "AI-Desk-Watchdog-0950",
                              "AI-Desk-IntradayLoop", "AI-Desk-OptionsWatch-1510"]))
        with mock.patch.object(morning_check.os, "name", "nt"), \
             mock.patch.object(subprocess, "run", return_value=out) as run:
            ok, detail = morning_check.check_schedules()
        self.assertTrue(ok, detail)
        self.assertIn("schtasks", str(run.call_args))

    def test_schedules_uses_launchctl_on_darwin(self):
        out = Proc("ai-cio-options\nai-cio-guard\nai-cio-desk\nai-cio-screen")
        with mock.patch.object(morning_check.os, "name", "posix"), \
             mock.patch.object(subprocess, "run", return_value=out):
            ok, detail = morning_check.check_schedules()
        self.assertTrue(ok, detail)

    def test_schedules_fail_closed_when_no_tasks(self):
        with mock.patch.object(morning_check.os, "name", "nt"), \
             mock.patch.object(subprocess, "run", return_value=Proc("")):
            ok, detail = morning_check.check_schedules()
        self.assertFalse(ok)
        self.assertIn("0/", detail)

    def test_viewer_targets_actual_port(self):
        self.assertEqual(morning_check.VIEWER_URL, "http://127.0.0.1:8766/api/health")


if __name__ == "__main__":
    unittest.main()
