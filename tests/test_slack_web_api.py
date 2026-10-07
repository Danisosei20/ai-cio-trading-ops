from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from robinhood_tools.errors import PolicyViolation
from robinhood_tools.slack_web_api import SlackWebApiNotifier, SlackWebApiReplyHost


class SlackWebApiReplyHostTests(unittest.TestCase):
    def test_fixed_channel_notifier_posts_paper_summary(self):
        calls = []

        def transport(method, payload, token):
            calls.append((method, payload, token))
            return {"ok": True, "ts": "2.1"}

        notifier = SlackWebApiNotifier(
            bot_token="xoxb-test", allowed_channel_id="C1", transport=transport,
        )
        result = notifier.send_approval(channel_id="C1", message="Paper trade summary")
        self.assertEqual(result["message_ts"], "2.1")
        self.assertEqual(calls[0], (
            "chat.postMessage", {"channel": "C1", "text": "Paper trade summary"}, "xoxb-test",
        ))
        with self.assertRaises(PolicyViolation):
            notifier.send_approval(channel_id="C2", message="wrong channel")

    def test_fixed_channel_notifier_uploads_image_with_threaded_comment(self):
        calls = []

        def transport(method, payload, token):
            calls.append((method, payload, token))
            return {"ok": True, "ts": "2.1", "permalink": "https://slack.test/message"}

        def upload_transport(method, payload, token, file_bytes, filename, content_type):
            calls.append((method, payload, token, file_bytes, filename, content_type))
            return {"ok": True, "file": {"id": "F123", "permalink": "https://slack.test/file/F123"}}

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "chart.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            notifier = SlackWebApiNotifier(
                bot_token="xoxb-test", allowed_channel_id="C1",
                transport=transport, upload_transport=upload_transport,
            )
            result = notifier.send_approval(
                channel_id="C1", message="Chart summary", image_path=image_path,
                image_alt_text="AAPL pattern",
            )

        self.assertEqual(result["message_ts"], "2.1")
        self.assertEqual(result["file_id"], "F123")
        self.assertEqual(result["file_permalink"], "https://slack.test/file/F123")
        self.assertEqual(calls[0], (
            "chat.postMessage", {"channel": "C1", "text": "Chart summary"}, "xoxb-test",
        ))
        self.assertEqual(calls[1][0], "files.upload")
        self.assertEqual(calls[1][1]["channels"], "C1")
        self.assertEqual(calls[1][1]["thread_ts"], "2.1")
        self.assertEqual(calls[1][1]["initial_comment"], "AAPL pattern")
        self.assertEqual(calls[1][4], "chart.png")
        self.assertEqual(calls[1][5], "image/png")

    def test_reads_only_replies_from_exact_thread(self):
        calls = []

        def transport(method, payload, token):
            calls.append((method, payload, token))
            return {"ok": True, "messages": [
                {"ts": "1.0", "text": "parent"},
                {"ts": "1.1", "text": "YES"},
                {"ts": "1.2", "text": "Recorded", "bot_id": "B1"},
            ]}

        host = SlackWebApiReplyHost(
            bot_token="xoxb-test", user_token="xoxp-test", allowed_channel_id="C1", transport=transport,
        )
        self.assertEqual(host.replies(channel_id="C1", parent_message_ts="1.0"), [
            {"message_ts": "1.1", "text": "YES"},
        ])
        self.assertEqual(calls[0], ("conversations.replies", {"channel": "C1", "ts": "1.0"}, "xoxp-test"))

    def test_acknowledgement_stays_in_exact_thread(self):
        calls = []

        def transport(method, payload, token):
            calls.append((method, payload, token))
            return {"ok": True}

        host = SlackWebApiReplyHost(
            bot_token="xoxb-test", user_token="xoxp-test", allowed_channel_id="C1", transport=transport,
        )
        host.acknowledge(channel_id="C1", parent_message_ts="1.0", message="Recorded")
        self.assertEqual(calls[0], ("chat.postMessage", {
            "channel": "C1", "thread_ts": "1.0", "text": "Recorded",
        }, "xoxb-test"))

    def test_other_channels_and_connector_errors_fail_closed(self):
        host = SlackWebApiReplyHost(
            bot_token="xoxb-test", user_token="xoxp-test", allowed_channel_id="C1",
            transport=lambda method, payload, token: {"ok": False, "error": "not_authed"},
        )
        with self.assertRaises(PolicyViolation):
            host.replies(channel_id="C2", parent_message_ts="1.0")
        with self.assertRaisesRegex(RuntimeError, "fail closed"):
            host.replies(channel_id="C1", parent_message_ts="1.0")

    def test_missing_configuration_is_rejected(self):
        with self.assertRaises(PolicyViolation):
            SlackWebApiReplyHost(bot_token="", user_token="xoxp-test", allowed_channel_id="C1")
        with self.assertRaises(PolicyViolation):
            SlackWebApiReplyHost(bot_token="xoxb-test", user_token="", allowed_channel_id="C1")
        with self.assertRaises(PolicyViolation):
            SlackWebApiReplyHost(bot_token="xoxb-test", user_token="xoxp-test", allowed_channel_id="")


if __name__ == "__main__":
    unittest.main()
