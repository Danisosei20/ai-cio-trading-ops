#!/usr/bin/env python3
"""Operational CLI for execution-authorization keys (no secret output)."""
from __future__ import annotations

import argparse
from pathlib import Path

from robinhood_tools import authorization_keys as keys


def main(argv: list[str] | None = None) -> tuple[int, str]:
    parser = argparse.ArgumentParser(description="Authorization key management")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="show key metadata (never key material)")
    sub.add_parser("initialize", help="create the first signing key")
    sub.add_parser("rotate", help="activate a new key, keep the old for verification")
    p_revoke = sub.add_parser("revoke", help="revoke a key by ID")
    p_revoke.add_argument("kid")
    p_revoke.add_argument("--compromised", action="store_true")
    sub.add_parser("verify-store", help="verify keystore integrity")
    sub.add_parser("audit-verify", help="verify the authorization audit hash chain")
    p_auth = sub.add_parser("auth-revoke", help="revoke an authorization by ID")
    p_auth.add_argument("auth_id")
    p_auth.add_argument("--reason", default="operator action")
    p_parent = sub.add_parser("auth-revoke-parent", help="revoke a parent and its children")
    p_parent.add_argument("auth_id")
    p_parent.add_argument("--reason", default="operator action")
    p_key = sub.add_parser("auth-revoke-key", help="revoke all authorizations signed by a key")
    p_key.add_argument("kid")
    p_key.add_argument("--reason", default="key compromised")
    sub.add_parser("auth-revoke-all-entries", help="emergency global entry revocation")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2, "command required"

    store = keys.KeyStore()

    def audit_key(event: str, detail: str = "") -> None:
        try:
            from robinhood_tools import authorization_audit as audit
            audit.emit(event, outcome="ok", reason=detail)
        except Exception:  # noqa: BLE001 - audit never breaks key operations
            pass

    if args.command == "status":
        rows = store.status()
        lines = [f"{r['kid']} {r['status']} created={r['created_at']}"
                 f" verify_until={r['verify_until'] or '-'}" for r in rows]
        return 0, "\n".join(lines) or "no keys"
    if args.command == "initialize":
        kid = store.initialize()
        audit_key("key_initialized", kid)
        return 0, f"initialized {kid}"
    if args.command == "rotate":
        kid = store.rotate()
        audit_key("key_rotated", kid)
        return 0, f"activated {kid}"
    if args.command == "revoke":
        store.revoke(args.kid, compromised=args.compromised)
        audit_key("key_compromised" if args.compromised else "key_revoked", args.kid)
        return 0, f"revoked {args.kid}"
    if args.command == "verify-store":
        return (0, "OK") if store.verify_store() else (2, "FAIL: keystore integrity")
    if args.command == "audit-verify":
        from robinhood_tools import authorization_audit as audit
        path = Path(__file__).resolve().parents[1] / "outputs/paper/authorization_audit.jsonl"
        return (0, "OK") if audit.verify_chain(path) else (2, "FAIL: audit chain")
    from robinhood_tools import execution_authorization as ea
    root = Path(__file__).resolve().parents[1]
    if args.command == "auth-revoke":
        ok = ea.revoke_authorization(args.auth_id, root, args.reason)
        return (0, f"revoked {args.auth_id}") if ok else (2, "revoke failed")
    if args.command == "auth-revoke-parent":
        count = ea.revoke_parent(root, args.auth_id, args.reason)
        return 0, f"revoked parent and {count} descendants"
    if args.command == "auth-revoke-key":
        count = ea.revoke_by_key(root, args.kid, args.reason)
        return 0, f"revoked {count} authorizations for {args.kid}"
    if args.command == "auth-revoke-all-entries":
        ea.revoke_all_entries(root, "emergency global entry revocation")
        return 0, "global entry revocation active (exits remain available)"
    return 2, "unknown command"


if __name__ == "__main__":
    rc, text = main()
    print(text)
    raise SystemExit(rc)
