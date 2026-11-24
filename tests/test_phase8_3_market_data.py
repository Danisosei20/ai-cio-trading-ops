"""Phase 8.3: Alpaca 403 classification, feed validation and bar integrity."""
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone

from robinhood_tools import alpaca_market_data, bar_validation, market_data_health as mdh
from robinhood_tools import market_data_evidence as mde


def request(status, body, headers=None):
    return lambda endpoint, feed, symbol: (status, body, headers or {})


class AccessClassificationTests(unittest.TestCase):
    def test_alpaca_entitlement_403_classification(self):
        body = '{"message":"subscription does not permit querying recent SIP data"}'
        result = mdh.probe(request(403, body), endpoint="snapshot",
                           feed="sip", symbol="QQQ")
        self.assertEqual(result.category, "entitlement_realtime_sip")
        self.assertEqual(result.status, 403)
        self.assertNotIn("SECRET", str(result.as_dict()))

    def test_generic_403_is_not_entitlement(self):
        result = mdh.probe(request(403, '{"message":"invalid API key"}'),
                           endpoint="snapshot", feed="iex", symbol="QQQ")
        self.assertEqual(result.category, "authentication_or_permission")

    def test_unsupported_feed_rejected_before_request(self):
        result = mdh.probe(request(200, "{}"), endpoint="bars",
                           feed="delayed_sip", symbol="QQQ")
        self.assertEqual(result.category, "unsupported_feed")
        self.assertIsNone(result.status)

    def test_missing_credentials_fail_closed(self):
        with self.assertRaises(Exception):
            alpaca_market_data.AlpacaMarketDataHttpClient("", "")

    def test_stale_market_evidence_rejected(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        ev = mde.MarketDataEvidence(
            instrument="QQQ", source="alpaca", feed="iex", volume_basis="iex_only",
            timeframe="5m", retrieved_at=old, market_as_of=old,
            cache_status="cache_hit", freshness="fresh")
        with self.assertRaises(mde.EvidenceIncomplete):
            mde.validate_evidence(ev)

    def test_outage_and_rate_limit_classification(self):
        self.assertEqual(mdh.classify_http(503, "down"), "provider_outage")
        self.assertEqual(mdh.classify_http(429, "slow down"), "rate_limited")
        err = urllib.error.URLError("offline")
        result = mdh.from_url_error(err, endpoint="snapshot", feed="iex", symbol="QQQ")
        self.assertEqual(result.category, "connection_failure")

    def test_read_only_report_has_no_order_permission(self):
        report = mdh.read_only_health_report(
            [mdh.probe(request(403, '{"message":"subscription does not permit querying recent SIP data"}'),
                       endpoint="snapshot", feed="sip", symbol="QQQ")])
        self.assertEqual(report["orders_submitted"], 0)
        self.assertIn("none", report["entry_permission"])


class ClientHardeningTests(unittest.TestCase):
    def test_bars_reject_delayed_sip(self):
        client = alpaca_market_data.AlpacaMarketDataHttpClient(
            "PKTEST00000000000000000000000000", "secret")
        with self.assertRaises(Exception):
            client.stock_bars("QQQ", timeframe="5Min", start="2026-11-24T14:00:00Z",
                              feed="delayed_sip")

    def test_websocket_unsupported_feed_rejected(self):
        from robinhood_tools.realtime_stream import RealtimeStream
        with self.assertRaises(ValueError):
            RealtimeStream("k", "s", feed="delayed_sip")
        stream = RealtimeStream("k", "s", feed="test")
        self.assertTrue(stream.ws_url.endswith("/v2/test"))


class BarIntegrityTests(unittest.TestCase):
    def _bars(self, count=12):
        start = datetime(2026, 11, 24, 14, 30, tzinfo=timezone.utc)
        return [{"t": (start + timedelta(minutes=5 * i)).isoformat(),
                 "h": 101 + i * 0.1, "l": 100 + i * 0.1,
                 "c": 100.5 + i * 0.1, "v": 1000 + i} for i in range(count)]

    def test_valid_bars_produce_vwap_and_opening_range(self):
        result = bar_validation.validate_bars(
            self._bars(), opening_bars=6,
            now=datetime(2026, 11, 24, 15, 30, tzinfo=timezone.utc))
        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(result.vwap)
        self.assertEqual(result.opening_range["bars"], 6)
        self.assertEqual(result.volume_basis, "iex_only")

    def test_duplicate_and_gapped_bars_rejected(self):
        bars = self._bars()
        bars[3] = dict(bars[2])
        self.assertEqual(bar_validation.validate_bars(
            bars, now=datetime(2026, 11, 24, 15, 30, tzinfo=timezone.utc)).status,
            "rejected")
        clean = self._bars(13)
        clean.pop(5)
        result = bar_validation.validate_bars(
            clean,
            now=datetime(2026, 11, 24, 15, 30, tzinfo=timezone.utc))
        self.assertEqual(result.status, "rejected")
        self.assertTrue(result.gaps)

    def test_stale_bars_rejected(self):
        result = bar_validation.validate_bars(
            self._bars(), now=datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(result.status, "rejected")

    def test_short_breakdown_requires_below_vwap_and_or_low(self):
        ok, _ = bar_validation.short_breakdown(99, vwap=100, or_high=105, or_low=100)
        self.assertTrue(ok)
        bad, why = bar_validation.short_breakdown(101, vwap=100, or_high=105, or_low=100)
        self.assertFalse(bad)
        self.assertIn("VWAP", why)

    def test_regime_signature_supported(self):
        from robinhood_tools import market_prices, regime_compat
        self.assertEqual(regime_compat.market_regime_compatible(
            [1.0] * 10, require_long_term=False),
            market_prices.market_regime([1.0] * 10))


class AdversarialBarTests(BarIntegrityTests):
    def test_future_dated_bars_rejected(self):
        now = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)
        bars = [{"t": (now + timedelta(minutes=5 * (i + 1))).isoformat(),
                 "h": 101, "l": 100, "c": 100.5, "v": 100} for i in range(12)]
        result = bar_validation.validate_bars(bars, now=now)
        self.assertEqual(result.status, "rejected")
        self.assertIn("future", result.reason)

    def test_invalid_close_outside_range_rejected(self):
        now = datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc)
        bars = [{"t": (now + timedelta(minutes=5 * i)).isoformat(),
                 "h": 50, "l": 40, "c": 100, "v": 100} for i in range(12)]
        result = bar_validation.validate_bars(
            bars, now=datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(result.status, "rejected")

    def test_non_dict_bar_rejected(self):
        bars = self._bars()
        bars[2] = "bad"
        self.assertEqual(bar_validation.validate_bars(bars).status, "rejected")

    def test_naive_entitlement_text_is_not_treated_as_sip(self):
        result = mdh.classify_http(403, '{"message":"your subscription key is invalid"}')
        self.assertEqual(result, "authentication_or_permission")


if __name__ == "__main__":
    unittest.main()
