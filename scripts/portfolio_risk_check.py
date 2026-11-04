#!/usr/bin/env python3
"""CLI gate for the central portfolio risk controller.

Usage:
  python scripts/portfolio_risk_check.py --symbol NVDA --side buy --notional 500 --strategy overnight
  python scripts/portfolio_risk_check.py --engage-kill --reason "manual halt"
  python scripts/portfolio_risk_check.py --clear-kill
  python scripts/portfolio_risk_check.py --status

Exit 0 = approved (or command succeeded). Exit 1 = rejected.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robinhood_tools.portfolio_risk import PortfolioRiskController
from decimal import Decimal


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Portfolio risk gate.")
    p.add_argument("--symbol", default="")
    p.add_argument("--side", default="buy", choices=["buy", "sell"])
    p.add_argument("--notional", type=float, default=0)
    p.add_argument("--strategy", default="unknown")
    p.add_argument("--data-age", type=float, default=0)
    p.add_argument("--equity", type=float, default=0)
    p.add_argument("--engage-kill", action="store_true")
    p.add_argument("--clear-kill", action="store_true")
    p.add_argument("--status", action="store_true")
    args = p.parse_args(argv)

    ctrl = PortfolioRiskController()

    if args.engage_kill:
        ctrl.engage_kill_switch("CLI")
        print("kill switch engaged")
        return 0
    if args.clear_kill:
        ctrl.clear_kill_switch()
        print("kill switch cleared")
        return 0
    if args.status:
        print(json.dumps(ctrl.status(), indent=2))
        return 0

    if not args.symbol:
        print("error: --symbol required")
        return 1

    verdict = ctrl.check(args.symbol, args.side, Decimal(str(args.notional)),
                         args.strategy, args.data_age, args.equity)
    print(json.dumps({"approved": verdict.approved, "reason": verdict.reason,
                      "checks": verdict.checks}))
    if verdict.approved:
        ctrl.record_order(args.symbol, args.side, args.strategy, args.notional)
    return 0 if verdict.approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
