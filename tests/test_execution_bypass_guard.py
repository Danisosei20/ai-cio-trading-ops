"""CI must catch new direct entry-order submissions outside the gateway."""
import tempfile
import unittest
from pathlib import Path

from scripts import check_execution_bypass as guard


class BypassGuardTests(unittest.TestCase):
    def test_known_submission_sites_are_allowlisted(self):
        self.assertTrue(guard.is_allowlisted("robinhood_tools/alpaca_paper.py", 139))
        self.assertTrue(guard.is_allowlisted("robinhood_tools/alpaca_options.py", 265))
        self.assertFalse(guard.is_allowlisted("scripts/brand_new_strategy.py", 10))

    def test_new_direct_submit_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = root / "new_strategy.py"
            f.write_text("resp = backend.place_equity_order(request)\n", encoding="utf-8")
            findings = guard.scan(root)
        self.assertTrue(any("new_strategy.py" in x for x in findings))

    def test_allowlisted_file_with_direct_call_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "robinhood_tools"
            d.mkdir()
            (d / "alpaca_paper.py").write_text(
                "resp = self.client.post('/v2/orders', json=body)\n", encoding="utf-8")
            findings = guard.scan(root)
        self.assertEqual(findings, [])

    def test_alternative_http_patterns_are_caught(self):
        samples = [
            "requests.post(url, json=body)",
            "httpx.post(url, json=body)",
            "transport.request(method, path, payload=b)",
            "getattr(transport, 'request')('POST', path, b)",
            "'/v2/ord' + 'ers'",
            'subprocess.run(["curl", "-X", "POST", url])',
        ]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = root / "new_http.py"
            f.write_text("\n".join(samples), encoding="utf-8")
            findings = guard.scan(root)
        self.assertTrue(findings)

    def test_comment_mention_is_not_a_finding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "notes.py").write_text(
                "# place_equity_order is the gateway\n", encoding="utf-8")
            self.assertEqual(guard.scan(root), [])


if __name__ == "__main__":
    unittest.main()
