#!/usr/bin/env python3
"""Explicit administrative first-run bootstrap for authorization stores.

Never called from trading startup. Leaves enforcement OFF and places no orders.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from robinhood_tools import authorization_audit as audit
from robinhood_tools import authorization_keys as keys
from robinhood_tools import execution_authorization as ea

CONFIRM = "INITIALIZE-AUTH-STORE"


def initialize(root: Path) -> dict:
    root = Path(root)
    keystore = Path(os.environ.get("CIO_AUTH_KEYSTORE", "")) or (root / "secure" / "keys.json")
    if keystore.is_symlink() or keystore.parent.is_symlink():
        raise ValueError("unsafe keystore path (symlink/junction)")
    report = {
        "schema": "auth-bootstrap-v1",
        "keystore": str(keystore),
        "enforcement": "off",
        "orders_submitted": 0,
    }
    if keystore.exists():
        store = keys.KeyStore(keystore)
        if store.verify_store():
            raise ValueError("valid keystore already exists; no overwrite performed")
    store = keys.KeyStore(keystore)
    report["active_key_id"] = store.initialize()
    p = root / "outputs/paper/authorization_audit.jsonl"
    if p.exists() and not audit.verify_chain(p):
        raise ValueError("existing audit chain invalid; bootstrap refused")
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        audit.emit("authorization_store_bootstrapped", path=p,
                   kid=report["active_key_id"], outcome="ok")
    ea.load_authorization("bootstrap-check", root)  # initializes DB schema
    report["audit_chain"] = "ok" if audit.verify_chain(p) else "invalid"
    report["store_integrity"] = "ok" if store.verify_store() else "invalid"
    return report


def main(argv: list[str] | None = None) -> tuple[int, str]:
    p = argparse.ArgumentParser(description="Authorization store bootstrap")
    sub = p.add_subparsers(dest="command")
    init = sub.add_parser("initialize")
    init.add_argument("--root", required=True)
    init.add_argument("--admin-confirm", default="")
    args = p.parse_args(argv)
    if args.command != "initialize":
        return 2, "command required: initialize"
    if args.admin_confirm != CONFIRM:
        return 2, f"explicit --admin-confirm {CONFIRM} required"
    try:
        report = initialize(Path(args.root))
    except (ValueError, OSError, keys.SecurityError) as exc:
        return 2, f"bootstrap refused: {type(exc).__name__}: {exc}"
    return 0, json.dumps(report, sort_keys=True)


if __name__ == "__main__":
    rc, text = main()
    print(text)
    raise SystemExit(rc)
