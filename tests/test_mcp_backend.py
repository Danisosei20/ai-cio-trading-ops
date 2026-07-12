from __future__ import annotations

import unittest
from decimal import Decimal

from robinhood_tools.errors import PolicyViolation
from robinhood_tools.mcp_backend import McpRobinhoodBackend
from robinhood_tools.models import EquityOrderRequest, OptionLeg, OptionOrderRequest


class RecordingRunner:
    def __init__(self):
        self.calls = []

    def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        if tool_name == "_review_equity_order":
            return {"ref_id": "review-1", "account_number": arguments["account_number"], "estimated_total": "100.00"}
        if tool_name == "_place_equity_order":
            return {"id": "order-1", "account_number": arguments["account_number"], "symbol": arguments["symbol"], "state": "queued"}
        if tool_name == "_review_option_order":
            return {"ref_id": "option-review-1", "account_number": arguments["account_number"], "alerts": ["review me"]}
        raise AssertionError(tool_name)


class McpRobinhoodBackendTests(unittest.TestCase):
    def setUp(self):
        self.runner = RecordingRunner()
        self.backend = McpRobinhoodBackend(self.runner)

    def test_maps_equity_review_to_robinhood_mcp_arguments(self):
        request = EquityOrderRequest(
            account_id="1234",
            symbol="VOO",
            side="buy",
            order_type="market",
            time_in_force="gfd",
            notional=Decimal("100.00"),
        )
        review = self.backend.review_equity_order(request)
        self.assertEqual(review.review_id, "review-1")
        self.assertEqual(
            self.runner.calls[0],
            (
                "_review_equity_order",
                {
                    "account_number": "1234",
                    "symbol": "VOO",
                    "side": "buy",
                    "type": "market",
                    "time_in_force": "gfd",
                    "market_hours": "regular_hours",
                    "dollar_amount": "100.00",
                },
            ),
        )

    def test_maps_equity_place_ref_id_to_robinhood_mcp_ref_id(self):
        request = EquityOrderRequest(
            account_id="1234",
            symbol="VOO",
            side="buy",
            order_type="market",
            time_in_force="gfd",
            quantity=Decimal("1"),
        )
        order = self.backend.place_equity_order(request, review_id="same-id-on-retry")
        self.assertEqual(order.id, "order-1")
        self.assertEqual(self.runner.calls[0][0], "_place_equity_order")
        self.assertEqual(self.runner.calls[0][1]["ref_id"], "same-id-on-retry")

    def test_rejects_mcp_unsupported_equity_time_in_force(self):
        request = EquityOrderRequest(
            account_id="1234",
            symbol="VOO",
            side="buy",
            order_type="market",
            time_in_force="ioc",
            quantity=Decimal("1"),
        )
        with self.assertRaisesRegex(PolicyViolation, "gfd or gtc"):
            self.backend.review_equity_order(request)

    def test_maps_single_leg_option_review_to_robinhood_mcp_arguments(self):
        request = OptionOrderRequest(
            account_id="1234",
            legs=(
                OptionLeg(
                    symbol="VOO",
                    side="buy",
                    effect="open",
                    option_type="call",
                    expiration_date="2026-12-18",
                    strike_price=Decimal("500"),
                    quantity=1,
                    option_id="option-uuid",
                ),
            ),
            order_type="limit",
            time_in_force="gfd",
            limit_price=Decimal("2.50"),
        )
        review = self.backend.review_option_order(request)
        self.assertEqual(review.review_id, "option-review-1")
        self.assertEqual(self.runner.calls[0][0], "_review_option_order")
        self.assertEqual(self.runner.calls[0][1]["legs"][0]["option_id"], "option-uuid")
        self.assertEqual(self.runner.calls[0][1]["price"], "2.50")

    def test_option_review_requires_option_id_for_mcp(self):
        request = OptionOrderRequest(
            account_id="1234",
            legs=(
                OptionLeg(
                    symbol="VOO",
                    side="buy",
                    effect="open",
                    option_type="call",
                    expiration_date="2026-12-18",
                    strike_price=Decimal("500"),
                    quantity=1,
                ),
            ),
            order_type="limit",
            time_in_force="gfd",
            limit_price=Decimal("2.50"),
        )
        with self.assertRaisesRegex(PolicyViolation, "option_id"):
            self.backend.review_option_order(request)


if __name__ == "__main__":
    unittest.main()
