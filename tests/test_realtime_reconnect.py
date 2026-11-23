"""Reconnect must be bounded, back off, and invoke post-reconnect reconciliation."""
import asyncio
import unittest
from unittest import mock

from robinhood_tools.realtime_stream import RealtimeStream


class _WS:
    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.closed = False

    async def close(self):
        self.closed = True


class ReconnectTests(unittest.TestCase):
    def test_reconnect_method_exists(self):
        self.assertTrue(hasattr(RealtimeStream, "_reconnect"))

    def test_reconnect_uses_backoff_and_invokes_reconciliation(self):
        stream = RealtimeStream("k", "s", max_reconnect_attempts=3,
                                reconnect_base_delay=0.01)
        stream._running = True
        stream._websocket = _WS()
        calls = []
        reconciled = []

        async def fake_connect():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("down")

        async def fake_sleep(seconds):
            reconciled.append(seconds)

        stream._connect = fake_connect
        stream.on_reconnect = lambda: reconciled.append("reconcile") or None

        with mock.patch("robinhood_tools.realtime_stream.asyncio.sleep", fake_sleep):
            asyncio.run(stream._reconnect())

        self.assertEqual(len(calls), 3)
        self.assertEqual(stream._reconnect_count, 0)
        self.assertIn("reconcile", reconciled)
        self.assertTrue(any(isinstance(x, float) for x in reconciled))

    def test_reconnect_stops_at_attempt_cap(self):
        stream = RealtimeStream("k", "s", max_reconnect_attempts=2,
                                reconnect_base_delay=0.001)
        stream._running = True
        calls = []

        async def fake_connect():
            calls.append(1)
            raise ConnectionError("down")

        async def fake_sleep(seconds):
            return None

        stream._connect = fake_connect
        with mock.patch("robinhood_tools.realtime_stream.asyncio.sleep", fake_sleep):
            asyncio.run(stream._reconnect())
        self.assertEqual(len(calls), 2)
        self.assertFalse(stream._running)

    def test_run_loop_uses_reconnect_not_crash_on_disconnect(self):
        stream = RealtimeStream("k", "s", max_reconnect_attempts=1)
        seen = []

        async def fake_connect():
            seen.append("connect")
            stream._websocket = _WS()
            if len(seen) == 1:
                raise ConnectionError("closed")

        async def fake_reconnect():
            seen.append("reconnect")
            stream._running = False

        stream._connect = fake_connect
        stream._reconnect = fake_reconnect
        asyncio.run(stream._run_loop())
        self.assertEqual(seen, ["connect", "reconnect"])


if __name__ == "__main__":
    unittest.main()
