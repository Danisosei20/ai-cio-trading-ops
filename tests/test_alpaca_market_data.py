from __future__ import annotations

import json
import unittest
from unittest import mock

from robinhood_tools.alpaca_market_data import AlpacaMarketDataHttpClient
from robinhood_tools.errors import PolicyViolation


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class AlpacaMarketDataTests(unittest.TestCase):
    def test_live_data_base_is_fixed_and_feed_is_allowlisted(self):
        with self.assertRaisesRegex(PolicyViolation, "data.alpaca.markets"):
            AlpacaMarketDataHttpClient("key", "secret", base_url="https://example.com")
        client = AlpacaMarketDataHttpClient("key", "secret")
        with self.assertRaisesRegex(PolicyViolation, "feed"):
            client.stock_snapshot("AAPL", feed="unknown")

    @mock.patch("robinhood_tools.alpaca_market_data.urlopen")
    def test_snapshot_bars_and_news_are_read_only(self, opened):
        opened.side_effect = [
            Response({"latestTrade": {"p": 200}}),
            Response({"bars": [{"c": 200, "v": 1000}]}),
            Response({"news": [{"headline": "Company update"}]}),
        ]
        client = AlpacaMarketDataHttpClient("key", "secret")
        self.assertEqual(client.stock_snapshot("aapl")["latestTrade"]["p"], 200)
        self.assertEqual(client.stock_bars("AAPL", timeframe="5Min", start="2026-07-14")[0]["c"], 200)
        self.assertEqual(client.news("AAPL", start="2026-07-13")[0]["headline"], "Company update")
        self.assertTrue(all(call.args[0].method == "GET" for call in opened.call_args_list))


if __name__ == "__main__":
    unittest.main()
