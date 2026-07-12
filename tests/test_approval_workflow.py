from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from robinhood_tools.notifications import JsonDeliveryLog, delivery_attempt


class ApprovalWorkflowTests(unittest.TestCase):
    def test_learning_journal_includes_market_signals_and_outcomes(self):
        journal_script = Path.home() / ".codex/skills/ai-cio-portfolio-manager/scripts/update_journal.py"
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "journal.csv"
            result = subprocess.run(
                [sys.executable, str(journal_script), str(journal), "--symbol", "AAPL", "--relative_volume", "1.2x", "--outcome_20d", "4%", "--benchmark_20d", "2%", "--excess_return_20d", "2%"],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            content = journal.read_text()
            self.assertIn("relative_volume", content)
            self.assertIn("excess_return_20d", content)
            self.assertIn("1.2x", content)

    def test_failed_slack_delivery_is_recorded_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deliveries.json"
            log = JsonDeliveryLog(path)
            log.append(delivery_attempt(
                "approval-1", "C1", status="failed", retry_count=2, error="connector unavailable"
            ))
            row = json.loads(path.read_text())["deliveries"][0]
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["retry_count"], 2)

    def test_health_check_does_not_post(self):
        result = subprocess.run(
            [sys.executable, "scripts/health_check.py", "--available-tools", "slack._slack_send_message", "--robinhood-read-ok"],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no message posted", result.stdout)

    def test_renderer_marks_slack_as_notification_only(self):
        result = subprocess.run(
            [sys.executable, "scripts/render_approval_message.py", "--account", "Agentic masked", "--recommendation", "Add", "--symbol", "VOO", "--side", "buy", "--order-type", "market", "--current-price", "500", "--intended-price", "500", "--price-as-of", "2026-07-10T14:00:00Z", "--price-source", "Robinhood", "--session-volume", "1000000", "--avg-volume-20d", "900000", "--avg-volume-50d", "850000", "--relative-volume", "1.1x", "--bid-ask-spread-pct", "0.02%", "--order-pct-avg-volume", "0.001%", "--volatility-20d", "18% annualized", "--next-earnings-date", "N/A test", "--signal-summary", "supportive", "--invalidation-level", "$475", "--target-review-condition", "$550 or thesis change", "--amount", "100", "--thesis", "Diversification", "--counter-argument", "Valuation", "--probability", "60%", "--reward-risk", "2:1", "--research-sources", "S&P DJI", "--research-sources", "SEC filing", "--approval-id", "approval-1"],
            check=True, capture_output=True, text=True,
        )
        self.assertIn("Expires:", result.stdout)
        self.assertIn("Replying in Slack does not approve execution.", result.stdout)
        self.assertIn("Current price: $500", result.stdout)
        self.assertIn("Intended purchase/sale price: $500", result.stdout)
        self.assertIn("relative volume 1.1x", result.stdout)
        self.assertIn("Signal summary: supportive", result.stdout)


if __name__ == "__main__":
    unittest.main()
