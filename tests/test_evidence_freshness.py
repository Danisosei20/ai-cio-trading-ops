"""Stale or failed source evidence must never be presented as current.

Certification audit findings A (Screen) and B (Liquid): the Screen station read
a previous-day screener after a failed producer run, and the Liquid station had
no age check on its signals cache. Both are fail-closed research inputs.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from scripts import desk_fleet

ET = ZoneInfo("America/New_York")


class Proc:
    def __init__(self, returncode=0):
        self.returncode = returncode


def _iso(minutes_old=0):
    return (datetime.now(ET) - timedelta(minutes=minutes_old)).isoformat()


class EvidenceFreshnessTests(unittest.TestCase):
    def _screener(self, root, *, minutes_old=0):
        d = Path(root) / "outputs" / "screener"
        d.mkdir(parents=True, exist_ok=True)
        doc = {"date": datetime.now(ET).date().isoformat(),
               "generated_at": _iso(minutes_old),
               "ranked": [{"symbol": "STALE"}]}
        (d / "latest.json").write_text(json.dumps(doc), encoding="utf-8")
        return doc

    def test_screen_rejects_stale_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            self._screener(root, minutes_old=600)
            with mock.patch.object(desk_fleet, "ROOT", Path(root)), \
                 mock.patch.object(desk_fleet, "_run", return_value=Proc(0)):
                res = desk_fleet.task_screen({"notes": [], "tickers": []},
                                             SimpleNamespace(task_top=1))
        self.assertEqual(res["tickers"], [])
        self.assertEqual(res["llm"]["status"], "unavailable")
        self.assertIn("stale", res["error"])

    def test_screen_rejects_artifact_after_failed_producer(self):
        with tempfile.TemporaryDirectory() as root:
            self._screener(root, minutes_old=1)
            with mock.patch.object(desk_fleet, "ROOT", Path(root)), \
                 mock.patch.object(desk_fleet, "_run", return_value=Proc(2)):
                res = desk_fleet.task_screen({"notes": [], "tickers": []},
                                             SimpleNamespace(task_top=1))
        self.assertEqual(res["tickers"], [])
        self.assertEqual(res["llm"]["status"], "unavailable")
        self.assertIn("producer failed", res["error"])

    def test_screen_accepts_fresh_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            self._screener(root, minutes_old=1)
            with mock.patch.object(desk_fleet, "ROOT", Path(root)), \
                 mock.patch.object(desk_fleet, "_run", return_value=Proc(0)):
                res = desk_fleet.task_screen({"notes": [], "tickers": []},
                                             SimpleNamespace(task_top=1))
        self.assertEqual(res["tickers"], ["STALE"])

    def test_screen_accepts_real_producer_shape_date_only(self):
        """universe_screen.py emits only `date`; that must not kill the station."""
        with tempfile.TemporaryDirectory() as root:
            d = Path(root) / "outputs" / "screener"
            d.mkdir(parents=True, exist_ok=True)
            (d / "latest.json").write_text(json.dumps({
                "date": datetime.now(ET).date().isoformat(),
                "universe": 100, "screened": 1,
                "ranked": [{"symbol": "REAL"}]}), encoding="utf-8")
            with mock.patch.object(desk_fleet, "ROOT", Path(root)), \
                 mock.patch.object(desk_fleet, "_run", return_value=Proc(0)):
                res = desk_fleet.task_screen({"notes": [], "tickers": []},
                                             SimpleNamespace(task_top=1))
        self.assertEqual(res["tickers"], ["REAL"])

    def _liquid(self, root, *, minutes_old):
        d = Path(root) / "outputs" / "liquid"
        d.mkdir(parents=True, exist_ok=True)
        doc = {"updated_at": _iso(minutes_old), "source": "test",
               "signals": {}, "positioning": {"long_heavy_crowd": [
                   {"symbol": "OLD"}]}, "leaderboard": [], "news": {}}
        (d / "latest.json").write_text(json.dumps(doc), encoding="utf-8")

    def test_liquid_rejects_stale_signals(self):
        with tempfile.TemporaryDirectory() as root:
            self._liquid(root, minutes_old=600)
            with mock.patch.object(desk_fleet, "ROOT", Path(root)):
                res = desk_fleet.task_liquid({"notes": [], "tickers": []},
                                             SimpleNamespace())
        self.assertEqual(res["crowded_longs"], [])
        self.assertEqual(res["llm"]["status"], "unavailable")
        self.assertIn("stale", res["note"])

    def test_liquid_accepts_fresh_signals(self):
        with tempfile.TemporaryDirectory() as root:
            self._liquid(root, minutes_old=1)
            with mock.patch.object(desk_fleet, "ROOT", Path(root)):
                res = desk_fleet.task_liquid({"notes": [], "tickers": []},
                                             SimpleNamespace())
        self.assertEqual(res["crowded_longs"], ["OLD"])


if __name__ == "__main__":
    unittest.main()
