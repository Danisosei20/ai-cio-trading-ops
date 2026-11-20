#!/usr/bin/env python3
"""Reconcile the desk ledger against the broker's fills.

The desk database held 49 approvals and zero fills: every fill lived only
at Alpaca, so no desk report could prove what had actually filled (the
2026-11-16 10:23 summary claimed 13 options entries; the broker held one
order). This run pulls the broker's fill activity into `order_fills`,
drives `trade_lifecycles` from observed fills, and reports orphan orders
and exits with no lifecycle instead of absorbing them silently.

Idempotent and read-mostly: fills are INSERT OR IGNORE and lifecycle
transitions only fire on the state that expects them, so running it twice
in a row records the second time nothing. Paper only: the transport is
pinned to paper-api.alpaca.markets by AlpacaPaperHttpTransport itself.

Usage:
  python scripts/reconcile_fills.py                # reconcile + write state
  python scripts/reconcile_fills.py --quiet         # state only, no stdout
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robinhood_tools.alpaca_paper import (  # noqa: E402
    AlpacaPaperBackend,
    AlpacaPaperHttpTransport,
)
from robinhood_tools.database import CioDatabase  # noqa: E402
from robinhood_tools.fill_reconciler import reconcile_desk_fills  # noqa: E402
from robinhood_tools.reconciliation import Fill  # noqa: E402
from robinhood_tools.runtime import build_settings  # noqa: E402
from robinhood_tools.settings import load_env  # noqa: E402

ET = ZoneInfo("America/New_York")
STATE_FILE = ROOT / "outputs" / "paper" / "fill_reconciliation.json"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Broker-fill reconciliation for the desk ledger.")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    p.add_argument("--max-pages", type=int, default=10,
                   help="activity pages to pull (100 fills each; default 10)")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def run(args) -> dict:
    settings = build_settings(args.config, args.env_file)
    env = load_env(args.env_file)
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    account_id = backend.list_accounts()[0].id
    db = CioDatabase(settings.database_path)

    orders = backend.list_all_orders(account_id=account_id)
    activities = backend.list_fill_activities(account_id=account_id,
                                              max_pages=args.max_pages)
    fills_by_order: dict[str, list[Fill]] = {}
    for activity in activities:
        order_id = str(activity.get("order_id", ""))
        if not order_id:
            continue
        fills_by_order.setdefault(order_id, []).append(Fill(
            fill_id=str(activity["id"]),
            quantity=Decimal(str(activity["qty"])),
            price=Decimal(str(activity["price"])),
            fee=Decimal("0"),
            filled_at=str(activity.get("transaction_time", "")),
        ))
    approvals_by_order = {str(row.get("order_id", "")): row
                          for row in db.list_approvals(100000)
                          if row.get("order_id")}

    report = reconcile_desk_fills(
        database=db, orders=orders, fills_by_order=fills_by_order,
        approvals_by_order=approvals_by_order,
        now_iso=datetime.now(ET).isoformat())
    payload = report.as_dict()

    try:
        db.audit("fill_reconciliation", payload,
                 correlation_id=f"reconcile:{uuid.uuid4().hex[:12]}")
    except Exception:  # noqa: BLE001 - the audit is a record, not the work
        pass
    return payload


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        payload = run(args)
    except Exception as exc:  # noqa: BLE001 - report a failed run, never crash the batch
        print(f"reconciliation failed: {type(exc).__name__}: {exc}")
        return 1

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if not args.quiet:
        print(json.dumps({
            "status": payload["status"],
            "orders_at_broker": payload["orders_at_broker"],
            "filled_orders": payload["filled_orders"],
            "fills_recorded": payload["fills_recorded"],
            "fills_already_known": payload["fills_already_known"],
            "lifecycles_opened": payload["lifecycles_opened"],
            "lifecycles_closed": payload["lifecycles_closed"],
            "orphan_orders": payload["orphan_orders"],
            "unattributed_exits": payload["unattributed_exits"],
            "errors": payload["errors"],
        }, indent=2))
    return 0 if payload["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())