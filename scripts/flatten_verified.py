#!/usr/bin/env python3
"""Broker-reconciled, read-only flatten verification.

Never places an order. Emits a durable terminal manifest:
NO_ACTION_REQUIRED only when broker evidence proves no owned day-trade
exposure remains; RECONCILIATION_REQUIRED otherwise. Protective exit scripts
remain separately available under their existing authorization policy.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from robinhood_tools.workflow_manifest import (NO_ACTION_REQUIRED,
                                               RECONCILIATION_REQUIRED,
                                               write_manifest)

ET = ZoneInfo("America/New_York")


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
    return {
        "positions": positions, "open_orders": open_orders,
        "account_equity": account.get("equity") if isinstance(account, dict) else None,
        "clock_is_open": clock.get("is_open") if isinstance(clock, dict) else None,
        "source": "read-only Alpaca paper GET",
    }


def owned_daytrade_symbols(root: Path, date: str) -> list[str]:
    out = set()
    for path in (root / "outputs/paper/tradingagents").glob(f"*/{date}/auto.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        if str(doc.get("action", "")).endswith("_placed") and \
                str(doc.get("trade_style", "")) == "day_trade":
            out.add(str(doc.get("ticker") or path.parent.parent.name).upper())
    return sorted(out)


def overnight_symbols(root: Path, date: str) -> set[str]:
    """Symbols owned by the overnight strategy; never flatten them here."""
    path = root / "outputs/paper/overnight.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return set()
    if str(doc.get("date", "")) != date:
        return set()
    return {str(e.get("ticker") or e.get("symbol", "")).upper()
            for e in doc.get("entries", []) if isinstance(e, dict)}


def _is_option(symbol: str) -> bool:
    # Alpaca option symbols look like "NVDA  250117C00150000".
    return " " in symbol or ("C" in symbol and len(symbol) > 12) or \
        ("P" in symbol and len(symbol) > 12)


def verify(root: Path, date: str) -> dict:
    started = datetime.now(ET).isoformat()
    try:
        state = broker_state(root)
    except Exception as exc:  # noqa: BLE001 - no broker evidence = no success
        error = f"broker read failed: {type(exc).__name__}: {exc}"
        result = write_manifest(
            root, task="flatten-verify", date=date,
            status=RECONCILIATION_REQUIRED, run_id=f"flatten-{date}",
            started_at=started, finished_at=datetime.now(ET).isoformat(),
            exit_code=1, mode="paper_read_only",
            evidence={"error": error[:300]}, error=error)
        return {"status": RECONCILIATION_REQUIRED, "exit_code": 1,
                "manifest": result, "error": error}

    owned = owned_daytrade_symbols(root, date)
    overnight = overnight_symbols(root, date)
    daytrade = set(owned) - overnight
    held = [str(p.get("symbol", "")).upper() for p in state["positions"]
            if float(p.get("qty", 0) or 0) and
            not _is_option(str(p.get("symbol", "")).upper())]
    related_open = [o for o in state["open_orders"]
                    if str(o.get("symbol", "")).upper() in daytrade]
    unresolved = sorted(daytrade & set(held))
    other_equity = sorted(set(held) - overnight - daytrade)
    # No local day-trade records is only safe when the broker has no equity
    # exposure outside the overnight strategy. Options stay in their own lane.
    if daytrade:
        ok = not unresolved and not related_open
    else:
        ok = not other_equity and not state["open_orders"]
    status = NO_ACTION_REQUIRED if ok else RECONCILIATION_REQUIRED
    evidence = {
        "positions": len(state["positions"]),
        "open_orders": len(state["open_orders"]),
        "owned_daytrade_symbols": owned,
        "overnight_symbols_excluded": sorted(overnight),
        "other_equity_positions": other_equity,
        "held_daytrade_symbols": unresolved,
        "related_open_orders": len(related_open),
        "equity": state["account_equity"],
        "broker_source": state["source"],
    }
    error = "" if ok else "owned day-trade exposure or pending exit unresolved; no order placed"
    result = write_manifest(
        root, task="flatten-verify", date=date, status=status,
        run_id=f"flatten-{date}", started_at=started,
        finished_at=datetime.now(ET).isoformat(),
        exit_code=0 if ok else 1, mode="paper_read_only",
        evidence=evidence, error=error)
    return {"status": status, "exit_code": 0 if ok else 1,
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
