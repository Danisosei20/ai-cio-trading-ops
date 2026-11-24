#!/usr/bin/env python3
"""Derive per-order child authorizations from a validated gate authorization.

The 09:45 authorization binds the deterministic decision, evidence and account.
A single intraday cycle can place several orders, so each risk-increasing order
receives its own short-lived child binding the actual symbol, side, quantity and
exposure. Children inherit the parent's decision identity and evidence hashes;
they are signed and atomically consumed at the broker boundary (ADR 0002).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import execution_authorization as ea

MAX_CHILDREN_PER_PARENT = 20


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def child_authorization(*, parent: dict, root: Path, symbol: str, side: str,
                        quantity: float, notional: float, risk: float,
                        strategy_id: str, ttl_seconds: int = 300,
                        position_effect: str = "open",
                        now: datetime | None = None) -> dict:
    """Create a signed child authorization bound to one order."""
    if not ea.verify_integrity(parent):
        raise ea.AuthorizationError("parent authorization failed integrity check")
    if parent.get("state") != "AUTHORIZED":
        raise ea.AuthorizationError("parent authorization is not AUTHORIZED")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    try:
        parent_expiry = _parse(parent["expires_at"])
        parent_issued = _parse(parent["issued_at"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ea.AuthorizationError("parent authorization has invalid timestamps") from exc
    if current > parent_expiry or current < parent_issued - timedelta(seconds=60):
        raise ea.AuthorizationError("parent authorization is expired or not yet valid")
    if side not in ("buy", "sell"):
        raise ea.AuthorizationError("unsupported order side")
    if position_effect not in ("open", "increase", "reduce", "close"):
        raise ea.AuthorizationError("unsupported position effect")
    if quantity <= 0 or notional <= 0 or risk <= 0:
        raise ea.AuthorizationError("child limits must be positive")
    if (quantity > float(parent.get("max_quantity", 0))
            or notional > float(parent.get("max_notional", 0))
            or risk > float(parent.get("max_risk", 0))):
        raise ea.AuthorizationError("child order exceeds parent authorization limits")
    if len(ea.children_of(str(parent.get("authorization_id", "")), root)) >= MAX_CHILDREN_PER_PARENT:
        raise ea.AuthorizationError("parent authorization child limit exhausted")

    digest_input = "|".join([
        str(parent.get("authorization_id", "")), str(symbol), side,
        str(quantity), str(notional), str(strategy_id), position_effect])
    child_id = "child-" + hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:24]
    expires = min(parent_expiry, current + timedelta(seconds=ttl_seconds))
    normalized = str(symbol).strip().upper()
    record = {
        "authorization_id": child_id,
        "parent_authorization_id": parent.get("authorization_id"),
        "decision_id": parent.get("decision_id"),
        "gate_run_id": parent.get("gate_run_id"),
        "strategy_id": strategy_id,
        "symbol": normalized,
        "instrument": f"{parent.get('broker', 'alpaca')}:{normalized}",
        "position_effect": position_effect,
        "side": side,
        "max_quantity": float(quantity),
        "max_notional": float(notional),
        "max_risk": float(risk),
        "broker": parent.get("broker", "alpaca"),
        "account_id": parent.get("account_id", ""),
        "environment": parent.get("environment", "paper"),
        "artifact_hashes": parent.get("artifact_hashes", {}),
        "evidence_paths": parent.get("evidence_paths", []),
        "gate_verdict": parent.get("gate_verdict", "proceed"),
        "gate_generated_at": parent.get("gate_generated_at", ""),
        "issued_at": current.isoformat(),
        "expires_at": expires.isoformat(),
        "state": "PENDING",
        "policy_version": parent.get("policy_version", ea.POLICY),
        "idempotency_key": child_id,
    }
    return ea._sign(record)


def derive_and_store(*, parent: dict, root: Path, symbol: str, side: str,
                     quantity: float, notional: float, risk: float,
                     strategy_id: str, ttl_seconds: int = 300,
                     position_effect: str = "open",
                     now: datetime | None = None) -> dict:
    """Derive, sign and persist a child; retries reuse the same idempotency key."""
    child = child_authorization(
        parent=parent, root=root, symbol=symbol, side=side, quantity=quantity,
        notional=notional, risk=risk, strategy_id=strategy_id,
        ttl_seconds=ttl_seconds, position_effect=position_effect, now=now)
    existing = ea.load_authorization(child["authorization_id"], root)
    if existing is not None:
        return existing
    return ea.store_authorization(child, root)


def attach_to_order(*, parent: dict, root: Path, order, now: datetime | None = None) -> dict:
    """Derive a child from a SignalOrder-like object and return its id."""
    quantity = float(order.quantity)
    limit = float(order.limit_price)
    notional = quantity * limit
    child = derive_and_store(
        parent=parent, root=root, symbol=order.symbol, side=order.side,
        quantity=quantity, notional=notional, risk=notional * 0.25,
        strategy_id=getattr(order, "strategy_id", "") or "orchestrator-research-long",
        now=now,
        position_effect=getattr(order, "position_effect", "open"))
    return child
