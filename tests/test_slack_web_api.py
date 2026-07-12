from __future__ import annotations

import unittest

from robinhood_tools.errors import PolicyViolation
from robinhood_tools.slack_web_api import SlackWebApiReplyHost


class SlackWebApiReplyHostTests(unittest.TestCase):
    def test_reads_only_replies_from_exact_thread(self):
        calls = []

        def transport(method, payload):
            calls.append((method, payload))
            return {"ok": True, "messages": [
                {"ts": "1.0", "text": "parent"},
                {"ts": "1.1", "text": "YES"},
            ]}

        host = SlackWebApiReplyHost(token="x-test", allowed_channel_id="C1", transport=transport)
        self.assertEqual(host.replies(channel_id="C1", parent_message_ts="1.0"), [
            {"message_ts": "1.1", "text": "YES"},
        ])
        self.assertEqual(calls[0], ("conversations.replies", {"channel": "C1", "ts": "1.0"}))

    def test_acknowledgement_stays_in_exact_thread(self):
        calls = []

        def transport(method, payload):
            calls.append((method, payload))
            return {"ok": True}

        host = SlackWebApiReplyHost(token="x-test", allowed_channel_id="C1", transport=transport)
        host.acknowledge(channel_id="C1", parent_message_ts="1.0", message="Recorded")
        self.assertEqual(calls[0], ("chat.postMessage", {
            "channel": "C1", "thread_ts": "1.0", "text": "Recorded",
        }))

    def test_other_channels_and_connector_errors_fail_closed(self):
        host = SlackWebApiReplyHost(
            token="x-test", allowed_channel_id="C1",
            transport=lambda method, payload: {"ok": False, "error": "not_authed"},
        )
        with self.assertRaises(PolicyViolation):
            host.replies(channel_id="C2", parent_message_ts="1.0")
        with self.assertRaisesRegex(RuntimeError, "fail closed"):
            host.replies(channel_id="C1", parent_message_ts="1.0")

    def test_missing_configuration_is_rejected(self):
        with self.assertRaises(PolicyViolation):
            SlackWebApiReplyHost(token="", allowed_channel_id="C1")
        with self.assertRaises(PolicyViolation):
            SlackWebApiReplyHost(token="x-test", allowed_channel_id="")


if __name__ == "__main__":
    unittest.main()
