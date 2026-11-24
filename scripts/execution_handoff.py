#!/usr/bin/env python3
"""09:45 deterministic decision -> bounded 10:20 execution authorization.

The entry gate is fail-closed: enforcement is disabled unless
``EXECUTION_AUTHORIZATION_ENABLED=true`` and a signing key is available.
This module contains no LLM code; it consumes only deterministic artifacts.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from robinhood_tools import execution_authorization as ea

INDEX = "outputs/paper/authorization_index.json"


def _run_dir(root: Path, run_id: str) -> Path:
    return Path(root) / "outputs" / "orchestrator" / run_id


def _evidence(root: Path, decision: dict) -> list[Path]:
    paths: list[Path] = []
    run_dir = _run_dir(root, str(decision.get("run_id", "")))
    decision_file = run_dir / "decision.json"
    if decision_file.is_file():
        paths.append(decision_file)
    for report in decision.get("reports", []) or []:
        for rel in report.get("evidence", []) or []:
            p = Path(root) / rel
            if p.is_file() and p not in paths:
                paths.append(p)
    return paths


def _index_path(root: Path) -> Path:
    return Path(root) / INDEX


def _read_index(root: Path) -> dict:
    try:
        return json.loads(_index_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_index(root: Path, index: dict) -> None:
    p = _index_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def context_for_run(root: Path, decision: dict) -> dict:
    """Conservative, deterministic order context derived from the gate run."""
    import os

    run_id = str(decision.get("run_id", ""))
    bars = sorted(_run_dir(root, run_id).glob("bars_*.json"))
    symbol = bars[0].stem.replace("bars_", "") if bars else "SPY"
    return {
        "strategy_id": "orchestrator-research-long",
        "symbol": symbol,
        "side": "buy",
        "max_quantity": 1,
        "max_notional": 1000.0,
        "max_risk": 25.0,
        "broker": "alpaca",
        "account_id": os.environ.get("ALPACA_ACCOUNT_ID", "paper-default"),
    }


def issue_for_run(root: Path, decision: dict) -> dict | None:
    """Issue the bounded authorization for a proceeding 09:45 run."""
    ctx = context_for_run(root, decision)
    return issue_authorization(root=root, decision=decision, **ctx)


def require_for_run(root: Path, decision: dict) -> tuple[bool, str]:
    """Revalidate the gate authorization without consuming it yet.

    Consumption belongs on the broker-submission boundary, where the actual
    symbol, side, quantity and notional are known.
    """
    ctx = context_for_run(root, decision)
    if not ea.enforcement_enabled():
        return False, "authorization enforcement flag disabled (ADR 0002 not approved)"
    record = load_for_run(root, str(decision.get("run_id", "")))
    if record is None:
        return False, "no execution authorization for this gate run"
    return ea.validate_authorization(
        record, root=root, decision=decision,
        artifacts=[],  # validator prefers the evidence set bound at issue time
        strategy_id=ctx["strategy_id"], symbol=ctx["symbol"], side=ctx["side"],
        quantity=ctx["max_quantity"], notional=ctx["max_notional"],
        risk=ctx["max_risk"], broker=ctx["broker"],
        account_id=ctx["account_id"], environment="paper")


def issue_authorization(*, root: Path, decision: dict, strategy_id: str, symbol: str,
                        side: str, max_quantity: int, max_notional: float,
                        max_risk: float, broker: str, account_id: str,
                        ttl_seconds: int = 300, now: datetime | None = None) -> dict | None:
    """Create and persist an authorization only for a clean ``proceed``."""
    if not ea.enforcement_enabled():
        return None
    if not isinstance(decision, dict) or decision.get("verdict") != "proceed":
        return None
    artifacts = _evidence(root, decision)
    if not artifacts:
        return None
    try:
        auth = ea.create_authorization(
            decision=decision, root=Path(root), artifacts=artifacts,
            strategy_id=strategy_id, symbol=symbol, side=side,
            max_quantity=max_quantity, max_notional=max_notional,
            max_risk=max_risk, broker=broker, account_id=account_id,
            environment="paper", ttl_seconds=ttl_seconds, now=now)
        stored = ea.store_authorization(auth, Path(root))
    except (ea.AuthorizationError, OSError, ValueError):
        return None
    index = _read_index(root)
    index[str(decision.get("run_id", ""))] = stored["authorization_id"]
    _write_index(root, index)
    return stored


def load_for_run(root: Path, run_id: str) -> dict | None:
    auth_id = _read_index(root).get(str(run_id))
    if not auth_id:
        return None
    return ea.load_authorization(auth_id, Path(root))


def require_execution_authorization(*, root: Path, run_id: str, decision: dict,
                                    strategy_id: str, symbol: str, side: str,
                                    quantity: float, notional: float, risk: float,
                                    broker: str, account_id: str,
                                    now: datetime | None = None) -> tuple[bool, str]:
    """The 10:20 boundary: no valid authorization means no entry order."""
    if not ea.enforcement_enabled():
        return False, "authorization enforcement flag disabled (ADR 0002 not approved)"
    record = load_for_run(root, run_id)
    if record is None:
        return False, "no execution authorization for this gate run"
    artifacts = _evidence(root, decision)
    return ea.gateway(
        authorization_id=record["authorization_id"], root=Path(root),
        decision=decision, artifacts=artifacts, strategy_id=strategy_id,
        symbol=symbol, side=side, quantity=quantity, notional=notional,
        risk=risk, broker=broker, account_id=account_id, environment="paper",
        now=now)
