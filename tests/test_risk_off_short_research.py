"""Shadow research must never turn a bearish signal into order permission."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.risk_off_short_research import main, shadow_report


BT = {
    "window_days": 60,
    "tickers": ["QQQ", "SPY"],
    "caveats": ["odds gate stubbed"],
    "totals": {"trades": 3, "regimes": {
        "RISK_ON": {"trades": 3, "net_pnl": -20.0}}},
    "per_ticker": [{"ticker": "QQQ", "regimes": {
        "RISK_ON": {"trades": 3, "net_pnl": -20.0}}}],
}


class ShadowTests(unittest.TestCase):
    def test_no_risk_off_samples_marks_insufficient(self):
        r = shadow_report(BT)
        self.assertTrue(r["shadow_only"])
        self.assertEqual(r["orders_submitted"], 0)
        self.assertEqual(r["risk_off_short_samples"], 0)
        self.assertEqual(r["evidence_sufficiency"], "insufficient")
        self.assertIn("none", r["entry_permission"])

    def test_risk_off_samples_still_do_not_grant_permission(self):
        bt = json.loads(json.dumps(BT))
        bt["totals"]["regimes"]["RISK_OFF"] = {"trades": 30, "net_pnl": 100.0}
        bt["per_ticker"][0]["regimes"]["RISK_OFF"] = {"trades": 30, "net_pnl": 100.0}
        r = shadow_report(bt)
        self.assertEqual(r["evidence_sufficiency"], "provisional")
        self.assertEqual(r["orders_submitted"], 0)
        self.assertEqual(r["entry_permission"],
                         "none - policy unchanged; RISK_OFF still blocks new risk desk-wide")

    def test_cli_writes_report_without_order_modules(self):
        import pathlib
        src_module = pathlib.Path(__import__("scripts.risk_off_short_research",
                                             fromlist=["x"]).__file__)
        text = src_module.read_text(encoding="utf-8")
        for forbidden in ("import robinhood_tools", "from robinhood_tools",
                          "place_signal_order", "alpaca_paper", "paper_flow"):
            self.assertNotIn(forbidden, text)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "bt.json"
            dst = Path(td) / "shadow.json"
            src.write_text(json.dumps(BT), encoding="utf-8")
            self.assertEqual(main(["--bt-json", str(src), "--out", str(dst)]), 0)
            r = json.loads(dst.read_text(encoding="utf-8"))
            self.assertTrue(r["shadow_only"])
            self.assertEqual(r["orders_submitted"], 0)


if __name__ == "__main__":
    unittest.main()
