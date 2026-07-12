#!/usr/bin/env python3
from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a CIO trade approval request.")
    parser.add_argument("--account", required=True)
    parser.add_argument("--recommendation", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", required=True)
    parser.add_argument("--order-type", required=True)
    parser.add_argument("--current-price", required=True)
    parser.add_argument("--intended-price", required=True)
    parser.add_argument("--price-as-of", required=True)
    parser.add_argument("--price-source", required=True)
    parser.add_argument("--session-volume", required=True)
    parser.add_argument("--avg-volume-20d", required=True)
    parser.add_argument("--avg-volume-50d", required=True)
    parser.add_argument("--relative-volume", required=True)
    parser.add_argument("--bid-ask-spread-pct", required=True)
    parser.add_argument("--order-pct-avg-volume", required=True)
    parser.add_argument("--volatility-20d", required=True)
    parser.add_argument("--next-earnings-date", required=True)
    parser.add_argument("--signal-summary", required=True)
    parser.add_argument("--invalidation-level", required=True)
    parser.add_argument("--target-review-condition", required=True)
    parser.add_argument("--amount", default="")
    parser.add_argument("--quantity", default="")
    parser.add_argument("--time-in-force", default="gfd")
    parser.add_argument("--thesis", required=True)
    parser.add_argument("--counter-argument", required=True)
    parser.add_argument("--probability", required=True)
    parser.add_argument("--reward-risk", required=True)
    parser.add_argument("--catalysts", default="")
    parser.add_argument("--portfolio-impact", default="")
    parser.add_argument("--alerts", default="None")
    parser.add_argument("--quote-disclosure", default="")
    parser.add_argument("--research-sources", action="append", default=[])
    parser.add_argument("--approval-id", default="")
    parser.add_argument("--approval-window-minutes", type=int, default=120)
    args = parser.parse_args()

    try:
        if Decimal(args.current_price) <= 0 or Decimal(args.intended_price) <= 0:
            parser.error("current-price and intended-price must be greater than zero")
    except InvalidOperation:
        parser.error("current-price and intended-price must be decimal numbers")
    if len(args.research_sources) < 2:
        parser.error("at least two research sources are required")

    approval_id = args.approval_id or str(uuid.uuid4())
    sizing = f"${args.amount}" if args.amount else f"{args.quantity} shares/contracts"
    print(f"# AI CIO Approval Request - {args.symbol}")
    print()
    print(f"Approval ID: {approval_id}")
    generated = datetime.now(timezone.utc)
    expires = generated + timedelta(minutes=args.approval_window_minutes)
    print(f"Generated: {generated.isoformat(timespec='seconds')}")
    print(f"Expires: {expires.isoformat(timespec='seconds')}")
    print()
    print(f"Recommendation: {args.recommendation}")
    print(f"Account: {args.account}")
    print(f"Order: {args.side} {sizing} of {args.symbol}")
    print(f"Type: {args.order_type}, TIF: {args.time_in_force}")
    print(f"Current price: ${args.current_price} as of {args.price_as_of} ({args.price_source})")
    print(f"Intended purchase/sale price: ${args.intended_price}")
    print(
        f"Volume: {args.session_volume} current; {args.avg_volume_20d} 20-day avg; "
        f"{args.avg_volume_50d} 50-day avg; relative volume {args.relative_volume}"
    )
    print(
        f"Execution: spread {args.bid_ask_spread_pct}; proposed order "
        f"{args.order_pct_avg_volume} of average volume"
    )
    print(f"20-day volatility: {args.volatility_20d}")
    print(f"Next earnings date: {args.next_earnings_date}")
    print(f"Signal summary: {args.signal_summary}")
    print(f"Invalidation level: {args.invalidation_level}")
    print(f"Target/review condition: {args.target_review_condition}")
    print()
    print(f"Thesis: {args.thesis}")
    print(f"Counter-argument: {args.counter_argument}")
    print(f"Probability: {args.probability}")
    print(f"Reward/risk: {args.reward_risk}")
    print(f"Catalysts: {args.catalysts or 'None specified'}")
    print(f"Portfolio impact: {args.portfolio_impact or 'Not specified'}")
    print(f"Broker alerts: {args.alerts}")
    if args.research_sources:
        print("Research sources:")
        for source in args.research_sources:
            print(f"- {source}")
    if args.quote_disclosure:
        print()
        print("Required quote disclosure:")
        print(args.quote_disclosure)
    print()
    print("To approve in Codex, reply with:")
    print(f"Approve {approval_id}: place the reviewed {sizing} {args.symbol} {args.side} order.")
    print("Replying in Slack does not approve execution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
