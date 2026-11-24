#!/usr/bin/env python3
"""Isolated Phase 7.3 rehearsal harness.

Uses production authorization modules with enforcement enabled only in the
calling process. Broker execution is mocked; no real orders are placed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from robinhood_tools import authorization_propagation as prop
from robinhood_tools import paper_flow
from scripts import execution_handoff as handoff

# The decision is frozen/historical; authorization timestamps must be
# current so the rehearsal exercises a live validity window.
DECISION = {"run_id": "rehearsal-0945", "verdict": "proceed",
            "generated_at": "2026-11-23T09:45:00-05:00"}


def _prepare(root: Path, scenario: str) -> dict:
    (root / "outputs/orchestrator/rehearsal-0945").mkdir(parents=True, exist_ok=True)
    (root / "outputs/paper").mkdir(parents=True, exist_ok=True)
    decision = dict(DECISION)
    if scenario == "halt":
        decision["verdict"] = "halt"
    (root / "outputs/orchestrator/rehearsal-0945/decision.json").write_text(
        json.dumps(decision, indent=2), encoding="utf-8")
    (root / "outputs/orchestrator/latest.json").write_text(
        json.dumps(decision), encoding="utf-8")
    evidence = root / "evidence.json"
    evidence.write_text(json.dumps({"source": "historical-replay",
                                    "date": "2026-11-23"}), encoding="utf-8")
    from robinhood_tools.authorization_audit import emit
    emit("authorization_created",
         path=root / "outputs/paper/authorization_audit.jsonl",
         authorization_id="rehearsal-seed", outcome="ok")
    return decision


def _entry(parent: dict, root: Path, decision: dict, symbol: str, side: str,
           notional: float) -> dict:
    child = prop.derive_and_store(parent=parent, root=root, symbol=symbol,
                                  side=side, quantity=1, notional=notional,
                                  risk=notional * 0.25, strategy_id="rehearsal",
                                  now=datetime.now(timezone.utc))
    order = paper_flow.SignalOrder(symbol=symbol, side=side,
                                   quantity=__import__("decimal").Decimal("1"),
                                   limit_price=__import__("decimal").Decimal(str(notional)),
                                   position_effect="open",
                                   authorization_id=child["authorization_id"],
                                   strategy_id="rehearsal")
    ok, reason = paper_flow.require_order_authorization(order, root)
    replay_ok, replay_reason = paper_flow.require_order_authorization(order, root)
    return {"authorized": ok, "reason": reason, "child_id": child["authorization_id"],
            "replay_blocked": not replay_ok, "replay_reason": replay_reason}


def run_rehearsal(root: Path, *, scenario: str = "proceed") -> dict[str, Any]:
    root = Path(root)
    decision = _prepare(root, scenario)
    parent = handoff.issue_authorization(
        root=root, decision=decision, strategy_id="rehearsal-parent",
        symbol="SPY", side="buy", max_quantity=20, max_notional=100000,
        max_risk=10000, broker="alpaca", account_id="paper-rehearsal",
        ttl_seconds=28800, now=datetime.now(timezone.utc))

    if parent is None:
        result = {"scenario": scenario, "gate_verdict": decision["verdict"],
                  "parent_authorization": None, "evidence_hashes": {},
                  "long_entry": {"authorized": False},
                  "short_entry": {"authorized": False},
                  "option_entry": {"authorized": False},
                  "replay_blocked": True, "exits_exempt": True}
    else:
        long = _entry(parent, root, decision, "AAPL", "buy", 100.0)
        short = _entry(parent, root, decision, "TSLA", "sell", 120.0)
        option = _entry(parent, root, decision, "AAPL261218C00150000", "buy", 200.0)
        exits = []
        for side, effect in (("sell", "close"), ("buy", "close")):
            order = paper_flow.SignalOrder(symbol="AAPL", side=side,
                                           quantity=__import__("decimal").Decimal("1"),
                                           limit_price=__import__("decimal").Decimal("100"),
                                           position_effect=effect)
            ok, _ = paper_flow.require_order_authorization(order, root)
            exits.append(ok)
        result = {"scenario": scenario, "gate_verdict": decision["verdict"],
                  "parent_authorization": parent["authorization_id"],
                  "evidence_hashes": parent["artifact_hashes"],
                  "long_entry": long, "short_entry": short, "option_entry": option,
                  "replay_blocked": long["replay_blocked"] and short["replay_blocked"]
                  and option["replay_blocked"],
                  "exits_exempt": all(exits)}

    result["audited_at"] = datetime.now(timezone.utc).isoformat()
    (root / "rehearsal_result.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result
