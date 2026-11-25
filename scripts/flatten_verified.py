#!/usr/bin/env python3
"""Broker-reconciled, read-only flatten verification.

Never places an order. The terminal status follows a precise ownership
contract encoded in the manifest evidence:

* VERIFIED / flat_scope=whole_account -- broker proves no positions and no
  open orders at all.
* NO_ACTION_REQUIRED / flat_scope=strategy_daytrade /
  ownership_label=VERIFIED_FLAT_STRATEGY -- every strategy-owned day-trade
  symbol is flat and has no working order; unrelated overnight, options or
  swing exposure is explicitly excluded rather than called "account flat".
* RECONCILIATION_REQUIRED -- anything unresolved, stale, missing or unknown.

Protective closes remain in flatten_daytrades.py, which always runs first.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from robinhood_tools.workflow_manifest import (NO_ACTION_REQUIRED,
                                               RECONCILIATION_REQUIRED, VERIFIED,
                                               write_manifest)

ET = ZoneInfo("America/New_York")
ACTIVE_ORDER_STATES = {"new", "open", "accepted", "pending_new", "working",
                       "partially_filled", "pending_cancel", "pending_replace"}
UNKNOWN_ORDER_STATES = {"unknown", "", None}
MAX_SNAPSHOT_AGE_SEC = 45.0


def _load_env(root: Path) -> dict:
    env = {}
    path = root / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    return {**env, **os.environ}


def broker_state(root: Path) -> dict:
    from robinhood_tools.alpaca_paper import (  # noqa: PLC0415
        AlpacaPaperBackend, AlpacaPaperHttpTransport)

    env = _load_env(root)
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    positions = backend.list_positions()
    open_orders = backend.list_open_orders()
    account = backend.transport.request("GET", "/v2/account")
    clock = backend.market_clock()
    if not isinstance(positions, list) or not isinstance(open_orders, list):
        raise ValueError("unexpected broker response shape: positions/orders not lists")
    return {
        "positions": positions, "open_orders": open_orders,
        "account_equity": account.get("equity") if isinstance(account, dict) else None,
        "clock_is_open": clock.get("is_open") if isinstance(clock, dict) else None,
        "queried_at": time.time(),
        "source": "read-only Alpaca paper GET",
    }


def _read_json(path: Path) -> tuple[dict | None, str]:
    if not path.exists():
        return None, ""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return None, f"{type(exc).__name__}: unreadable ownership artifact {path.name}"
    if not isinstance(doc, dict):
        return None, f"invalid ownership artifact {path.name}"
    return doc, ""


def ownership(root: Path, date: str) -> tuple[set[str], set[str], list[str]]:
    """Return (daytrade, overnight, errors). Errors always fail closed."""
    errors: list[str] = []
    owned: set[str] = set()
    pattern = f"*/{date}/auto.json"
    for path in sorted((root / "outputs/paper/tradingagents").glob(pattern)):
        doc, error = _read_json(path)
        if error:
            errors.append(error)
            continue
        if doc is None:
            errors.append(f"missing ownership artifact {path.name}")
            continue
        action = str(doc.get("action", ""))
        style = str(doc.get("trade_style", ""))
        if action.endswith("_placed") and style == "day_trade":
            symbol = str(doc.get("ticker") or path.parent.parent.name).upper()
            if not symbol:
                errors.append(f"day-trade record without symbol: {path.name}")
            else:
                owned.add(symbol)
        elif action and style and not action.endswith("_placed"):
            # A recorded order that was never placed is not owned exposure.
            continue

    path = root / "outputs/paper/overnight.json"
    doc, error = _read_json(path)
    overnight: set[str] = set()
    if error:
        errors.append(error)
    elif doc is not None:
        if str(doc.get("date", "")) != date:
            errors.append("overnight ownership artifact is for a different date")
        else:
            entries = doc.get("entries", [])
            if not isinstance(entries, list):
                errors.append("overnight ownership entries are not a list")
            else:
                overnight = {str(e.get("ticker") or e.get("symbol", "")).upper()
                             for e in entries if isinstance(e, dict)}
    overlap = owned & overnight
    if overlap:
        errors.append("symbol owned by both day-trade and overnight strategies: " +
                      ",".join(sorted(overlap)))
    return owned, overnight, errors


def _is_option(symbol: str) -> bool:
    # Alpaca option symbols look like "NVDA  250117C00150000".
    return " " in symbol or (len(symbol) > 12 and ("C" in symbol or "P" in symbol))


def _classify(state: dict, owned: set[str], overnight: set[str]) -> tuple[str, str, dict]:
    """Return (status, flat_scope, detail)."""
    reasons: list[str] = []
    positions = state["positions"]
    orders = state["open_orders"]

    all_positions = []
    equity_positions = []
    options = []
    per_symbol = {}
    for pos in positions:
        if not isinstance(pos, dict):
            reasons.append("malformed broker position record")
            continue
        symbol = str(pos.get("symbol", "")).upper()
        try:
            qty = float(pos.get("qty", 0) or 0)
        except (TypeError, ValueError):
            reasons.append(f"non-numeric quantity for {symbol}")
            continue
        if _is_option(symbol):
            options.append(symbol)
            continue
        if abs(qty) > 0:
            equity_positions.append(symbol)
            per_symbol[symbol] = {
                "qty": qty, "direction": "long" if qty > 0 else "short",
                "owner": ("daytrade" if symbol in owned else
                          "overnight" if symbol in overnight else "unrelated"),
            }
        all_positions.append(symbol)

    order_states = {}
    for order in orders:
        if not isinstance(order, dict):
            reasons.append("malformed broker order record")
            continue
        symbol = str(order.get("symbol", "")).upper()
        status = str(order.get("status", "")).lower()
        order_states[symbol] = status
        if status in UNKNOWN_ORDER_STATES or status not in ACTIVE_ORDER_STATES:
            reasons.append(f"unknown or non-terminal order state {status!r} on {symbol}")

    detail = {
        "per_symbol": per_symbol, "option_positions": sorted(set(options)),
        "open_order_statuses": order_states,
        "held_equity_symbols": sorted(set(equity_positions)),
    }
    daytrade_held = sorted(set(per_symbol) & owned)
    unrelated_held = sorted(set(per_symbol) - owned - overnight)
    daytrade_orders = sorted(s for s in order_states if s in owned)
    detail["held_daytrade_symbols"] = daytrade_held
    detail["unrelated_equity_symbols"] = unrelated_held
    detail["related_open_orders"] = daytrade_orders

    if daytrade_held:
        reasons.append("strategy-owned day-trade exposure remains: " +
                       ",".join(daytrade_held))
    if daytrade_orders:
        reasons.append("working day-trade order remains: " + ",".join(daytrade_orders))
    unattributed = set(per_symbol) - overnight
    if unattributed:
        # Without an explicit ownership record, a broker position cannot
        # be declared out of scope. Fail closed even when some day-trade
        # records exist (a missing/corrupt record is ambiguous).
        reasons.append("unattributed equity position: " +
                       ",".join(sorted(unattributed)))
    if reasons:
        return RECONCILIATION_REQUIRED, "", detail

    if not all_positions and not orders and not options:
        return VERIFIED, "whole_account", detail
    return NO_ACTION_REQUIRED, "strategy_daytrade", detail


def verify(root: Path, date: str) -> dict:
    started = datetime.now(ET).isoformat()
    state = None
    try:
        state = broker_state(root)
    except Exception as exc:  # noqa: BLE001 - no broker evidence = no success
        error = f"broker read failed: {type(exc).__name__}: {exc}"
        result = write_manifest(
            root, task="flatten-verify", date=date,
            status=RECONCILIATION_REQUIRED, run_id=f"flatten-{date}",
            started_at=started, finished_at=datetime.now(ET).isoformat(),
            exit_code=1, mode="paper_read_only",
            evidence={"error": error[:300], "source": "broker unavailable"},
            error=error)
        return {"status": RECONCILIATION_REQUIRED, "exit_code": 1,
                "manifest": result, "error": error}

    owned, overnight, errors = ownership(root, date)
    age = time.time() - float(state.get("queried_at", time.time()))
    if age > MAX_SNAPSHOT_AGE_SEC:
        errors.append(f"broker snapshot is {age:.1f}s old")
    status, scope, detail = _classify(state, owned, overnight)
    if errors:
        status, scope = RECONCILIATION_REQUIRED, ""
        detail["ownership_errors"] = errors
    else:
        detail["ownership_errors"] = []

    evidence = {
        "flat_scope": scope,
        "ownership_label": ("VERIFIED_FLAT_STRATEGY"
                            if status == NO_ACTION_REQUIRED else ""),
        "positions": len(state["positions"]),
        "open_orders": len(state["open_orders"]),
        "owned_daytrade_symbols": sorted(owned),
        "overnight_symbols_excluded": sorted(overnight),
        "snapshot_age_sec": round(age, 2),
        "broker_source": state["source"],
        **detail,
    }
    error = "" if status in {VERIFIED, NO_ACTION_REQUIRED} else (
        "exposure, ownership or broker evidence unresolved; no order placed")
    result = write_manifest(
        root, task="flatten-verify", date=date, status=status,
        run_id=f"flatten-{date}", started_at=started,
        finished_at=datetime.now(ET).isoformat(),
        exit_code=0 if status in {VERIFIED, NO_ACTION_REQUIRED} else 1,
        mode="paper_read_only", evidence=evidence, error=error)
    return {"status": status, "exit_code": 0 if status in {
        VERIFIED, NO_ACTION_REQUIRED} else 1,
        "manifest": result, "error": error, "evidence": evidence}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=datetime.now(ET).date().isoformat())
    ap.add_argument("--root", type=Path,
                    default=Path(__file__).resolve().parents[1])
    args = ap.parse_args(argv)
    result = verify(Path(args.root), args.date)
    print(json.dumps(result, indent=2, default=str))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
