#!/usr/bin/env python3
"""SHADOW-ONLY routing of bearish research into short-setup evaluation.

A bearish analyst decision is a research input, never an order. This module
maps such a decision to an *independent* technical short evaluation, reuses the
existing read-only validators, and emits a structured rejection or shadow
candidate. It never places an order, derives authorization, writes a broker
ledger, transitions a trade state machine, or records a fill.

New routing is shadow-only until backtest evidence, out-of-sample validation,
authorization coverage, broker checks, risk verification, independent review
and explicit owner approval are obtained.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

BUY_SIGNALS = {"buy", "overweight"}
BEARISH_SIGNALS = {"sell", "underweight"}


@dataclass(frozen=True)
class BearishResearch:
    symbol: str
    decision: str
    desk_verdict: str = ""
    regime: str = "UNKNOWN"
    source: str = ""

    @property
    def is_bearish(self) -> bool:
        return self.decision.strip().lower() in BEARISH_SIGNALS

    @property
    def automatic_short(self) -> bool:
        """An analyst verdict can never automatically become a short order."""
        return False


@dataclass(frozen=True)
class PositionIntent:
    side: str
    position_effect: str
    meaning: str


def position_effect_for(*, side: str, owned_qty: float,
                        direction: str, opening: bool) -> PositionIntent:
    """Explicitly classify the four order intents; side alone is ambiguous."""
    side = side.lower()
    owned_qty = abs(float(owned_qty))
    if opening:
        if side == "sell":
            return PositionIntent("sell", "open", "sell short to open a new short")
        if side == "buy":
            return PositionIntent("buy", "open", "buy to open a new long")
    if owned_qty > 0 and direction == "long" and side == "sell":
        return PositionIntent("sell", "close", "sell to close an existing long")
    if owned_qty > 0 and direction == "short" and side == "buy":
        return PositionIntent("buy", "close", "buy to cover an existing short")
    return PositionIntent(side, "unknown", "no verified owned position for this effect")


def rejection(gate: str, code: str, reason: str) -> dict:
    return {"gate": gate, "code": code, "reason": str(reason)[:300]}


def route_bearish_candidate(
    research: BearishResearch,
    *,
    setup: dict | None = None,
    equity: float | None = None,
    open_positions: dict | None = None,
    max_workers: int = 1,
    validate_setup: Callable | None = None,
    check_eligibility: Callable | None = None,
    check_risk: Callable | None = None,
    now: str | None = None,
) -> dict:
    """Evaluate one bearish research item under existing gates, shadow-only.

    Validators are injected so the function is pure and deterministic by
    default. When omitted, no broker/network call is made and the result is a
    fail-closed ``rejected`` record - never a shadow trade claim.
    """
    stamp = now or datetime.now(ET).isoformat()
    rejections: list[dict] = []
    if not research.is_bearish:
        rejections.append(rejection("research", "not_bearish",
                                    f"decision {research.decision!r} is not a bearish signal"))
    if research.automatic_short:  # pinned false; defensive only
        rejections.append(rejection("research", "automatic_short_forbidden",
                                    "bearish research must never auto-short"))
    held = (open_positions or {}).get(research.symbol, 0) if open_positions else 0
    if isinstance(held, dict):
        held = held.get("qty", held.get("quantity", 0))
    try:
        owned = float(held or 0)
    except (TypeError, ValueError):
        owned = 0.0
    intent = position_effect_for(
        side="sell", owned_qty=abs(owned),
        direction="short" if owned < 0 else "long",
        opening=owned == 0)

    record: dict[str, Any] = {
        "symbol": research.symbol,
        "decision": research.decision,
        "desk_verdict": research.desk_verdict,
        "research_source": research.source,
        "regime": research.regime,
        "direction": "short",
        "setup": (setup or {}).get("setup") if setup else None,
        "position_effect": intent.position_effect,
        "position_meaning": intent.meaning,
        "automatic_short": research.automatic_short,
        "shadow_only": True,
        "orders_submitted": 0,
        "enforcement_enabled": False,
        "entry_permission": "none - research only; RISK_OFF still blocks new risk desk-wide",
        "approved": False,
        "decision_class": "rejected",
        "plan": None,
        "eligibility": None,
        "risk": None,
        "rejections": rejections,
        "evaluated_at": stamp,
        "safety_markers": {
            "module_never_calls_order_or_authorization_apis": True,
            "no_broker_write": "guaranteed for this module only",
            "no_ledger_write": "guaranteed for this module only",
            "no_authorization_derived": "guaranteed for this module only",
            "no_fill_recorded": "guaranteed for this module only",
            "injected_validators": "caller responsibility - not verified here",
        },
    }
    if rejections:
        return record

    if not setup or not equity:
        record["rejections"].append(rejection("evidence", "missing_setup_or_equity",
                                              "point-in-time setup/equity unavailable"))
        return record
    if str(setup.get("setup") or "") != "short_breakdown":
        # A long-reclaim/range setup must never be fed to the short validator.
        record["rejections"].append(rejection(
            "setup", "setup_direction_mismatch",
            f"bearish research requires an independent short_breakdown setup; "
            f"got {setup.get('setup')!r}"))
        return record
    if intent.position_effect != "open":
        record["rejections"].append(rejection("position", "not_a_new_short",
                                              intent.meaning))
        return record
    if validate_setup is None or check_eligibility is None or check_risk is None:
        record["rejections"].append(rejection("adapter", "validators_not_supplied",
                                              "shadow evaluation requires injected read-only validators"))
        return record

    try:
        ok, reason, plan = validate_setup(setup, equity, open_positions or {})
    except Exception as exc:  # noqa: BLE001 - fail closed on validator error
        record["rejections"].append(rejection("setup", "validator_error", exc))
        return record
    def as_result(value):
        """Normalize dicts and risk dataclasses; never trust .get() to exist."""
        if isinstance(value, dict):
            return dict(value)
        try:
            from dataclasses import asdict, is_dataclass  # noqa: PLC0415
            if is_dataclass(value):
                return asdict(value)
        except Exception:  # noqa: BLE001
            pass
        return {"approved": bool(getattr(value, "approved", False)),
                "eligible": bool(getattr(value, "eligible", False)),
                "reason": str(getattr(value, "reason", "unverified"))}

    def finish():
        # A rejected record must never retain an actionable plan.
        if record["decision_class"] == "rejected":
            record["plan"] = None
        return record

    if not ok:
        record["rejections"].append(rejection("setup", "technical_setup_rejected", reason))
        return finish()
    record["plan"] = plan

    try:
        eligibility = as_result(check_eligibility(plan))
    except Exception as exc:  # noqa: BLE001
        record["rejections"].append(rejection("borrow", "eligibility_error", exc))
        return finish()
    record["eligibility"] = eligibility
    if not eligibility.get("eligible"):
        record["rejections"].append(rejection("borrow", "short_eligibility_failed",
                                              eligibility.get("reason", "unverified")))
        return finish()

    try:
        risk = as_result(check_risk({**plan, "equity": equity}))
    except Exception as exc:  # noqa: BLE001
        record["rejections"].append(rejection("risk", "risk_check_error", exc))
        return finish()
    record["risk"] = risk
    if not risk.get("approved"):
        record["rejections"].append(rejection("risk", "risk_blocked",
                                              risk.get("reason", "unverified")))
        return finish()

    record["decision_class"] = "shadow_candidate"
    record["note"] = ("Independent short setup passed read-only validation. "
                      "This is research output, not authorization, an order, or a fill.")
    return finish()


def read_only_adapter(root: Path | None = None):
    """Lazily bind the existing read-only validators (no order functions)."""
    import os  # noqa: PLC0415
    from decimal import Decimal  # noqa: PLC0415

    from robinhood_tools.alpaca_paper import AlpacaPaperHttpTransport  # noqa: PLC0415
    from robinhood_tools.settings import load_env  # noqa: PLC0415
    from scripts import daytrade_engine as engine  # noqa: PLC0415

    repo = Path(root) if root else Path(__file__).resolve().parents[1]
    env = {**load_env(str(repo / ".env")), **os.environ}
    transport = AlpacaPaperHttpTransport.from_values(env)

    def validate(setup, equity, open_positions):
        # The engine uses Decimal prices; JSON artifacts carry floats.
        prepared = dict(setup or {})
        for key in ("price", "or_high", "or_low", "vwap"):
            if prepared.get(key) is not None:
                prepared[key] = Decimal(str(prepared[key]))
        return engine._validate_setup(prepared, Decimal(str(equity)), open_positions)

    def eligibility(plan):
        from robinhood_tools.short_eligibility import fetch_short_eligibility  # noqa: PLC0415
        return fetch_short_eligibility(plan["ticker"], transport)

    def risk(plan):
        from robinhood_tools.portfolio_risk import PortfolioRiskController  # noqa: PLC0415
        return PortfolioRiskController().check(
            plan["ticker"], "sell", plan["qty"] * plan["entry"],
            strategy="intraday", data_age_sec=0,
            current_equity=plan.get("equity"), short=True)

    return {"validate_setup": validate, "check_eligibility": eligibility,
            "check_risk": risk}


def write_shadow_report(records: list[dict], path: Path, *, root: Path | None = None) -> dict:
    report = {
        "track": "risk_off_short_challenger_shadow",
        "generated_at": datetime.now(ET).isoformat(),
        "shadow_only": True,
        "orders_submitted": 0,
        "enforcement_enabled": False,
        "entry_permission": "none - policy unchanged; RISK_OFF still blocks new risk desk-wide",
        "candidates": records,
        "count": len(records),
        "note": "Shadow research only. No record here is an order, fill, or realized profit.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(__import__("json").dumps(report, indent=2, default=str) + "\n",
                    encoding="utf-8")
    return report
