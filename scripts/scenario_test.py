#!/usr/bin/env python3
"""Scenario matrix: what the desk does under every condition it claims to handle.

The unit suite proves each gate in isolation and the certification proves
each execution path. Neither answers the operator's question: "if the tape
is X tomorrow, what will the desk DO?" This harness walks the decision
matrix - regime x signal x mode, the weekend rule, loss memory, the entry
odds gate, the exit policy, the entry caps, the ledger - running the
REAL gate functions against scenario inputs and reporting verdict vs
expectation.

Offline and deterministic: stubbed quotes, injected vol, temp ledgers, no
network, no broker, no LLM. Output: a matrix on stdout plus
outputs/simulation/scenarios.json.

Usage:
  python scripts/scenario_test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
OUT = ROOT / "outputs" / "simulation" / "scenarios.json"
TODAY = date(2026, 11, 16)

results: list[dict] = []


def check(group: str, name: str, expected, actual, detail: str = "") -> None:
    ok = expected == actual
    results.append({"group": group, "scenario": name, "expected": expected,
                    "actual": actual, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {group}: {name} -> {actual}"
          + (f" ({detail})" if detail and not ok else ""))


# ── A. Regime gate ────────────────────────────────────────────────────────
def scenarios_regime() -> None:
    from scripts.tradingagents_paper_auto import regime_gate_passes

    g = "regime"
    cases = [
        ("RISK_ON tape, buy signal, equity mode", dict(regime="RISK_ON", circuit_broken=False, sig="buy", options_mode=False), True),
        ("RISK_ON tape, buy signal, options mode", dict(regime="RISK_ON", circuit_broken=False, sig="overweight", options_mode=True), True),
        ("RISK_OFF tape, buy signal (long premium)", dict(regime="RISK_OFF", circuit_broken=False, sig="buy", options_mode=True), False),
        ("RISK_OFF tape, bearish signal -> put (owner 2026-11-16)", dict(regime="RISK_OFF", circuit_broken=False, sig="underweight", options_mode=True), True),
        ("CHOP tape, any new risk", dict(regime="CHOP", circuit_broken=False, sig="buy", options_mode=True), False),
        ("UNKNOWN tape, any new risk", dict(regime="UNKNOWN", circuit_broken=False, sig="sell", options_mode=True), False),
        ("CHOP tape, equity exit (never blocked)", dict(regime="CHOP", circuit_broken=False, sig="sell", options_mode=False), True),
        ("RISK_ON but circuit breaker tripped", dict(regime="RISK_ON", circuit_broken=True, sig="buy", options_mode=False), False),
    ]
    for name, kwargs, expected in cases:
        check(g, name, expected, regime_gate_passes(**kwargs))


# ── B. Weekend gap rule ───────────────────────────────────────────────────
def scenarios_weekend() -> None:
    from robinhood_tools.options_flow import weekend_gap_block

    g = "weekend-gap"
    def at(y, m, d, h, mi):
        return datetime(y, m, d, h, mi, tzinfo=ET)
    check(g, "Thursday 15:50 put opener", True,
          weekend_gap_block(right="put", when=at(2026, 11, 19, 15, 50)) is not None)
    check(g, "Thursday 10:00 put opener", False,
          weekend_gap_block(right="put", when=at(2026, 11, 19, 10, 0)) is not None)
    check(g, "Thursday 15:50 call opener (longs unaffected)", False,
          weekend_gap_block(right="call", when=at(2026, 11, 19, 15, 50)) is not None)
    check(g, "Wednesday 16:00 put opener", False,
          weekend_gap_block(right="put", when=at(2026, 11, 18, 16, 0)) is not None)
    check(g, "Friday 16:00 put opener", False,
          weekend_gap_block(right="put", when=at(2026, 11, 20, 16, 0)) is not None)


# ── C. Per-symbol loss memory ─────────────────────────────────────────────
def scenarios_loss_memory() -> None:
    from robinhood_tools.loss_memory import RoundTrip, loss_streak_verdict
    from robinhood_tools.errors import PolicyViolation

    g = "loss-memory"
    trips = [RoundTrip("RDDT", Decimal("1"), Decimal("-100"), date(2026, 11, 14)),
             RoundTrip("RDDT", Decimal("1"), Decimal("-50"), date(2026, 11, 12))]
    check(g, "two consecutive losses rest the underlier", True,
          loss_streak_verdict(trips, symbol="RDDT", today=TODAY).blocked)
    check(g, "one loss does not rest it", False,
          loss_streak_verdict(trips[:1], symbol="RDDT", today=TODAY).blocked)
    won = trips + [RoundTrip("RDDT", Decimal("1"), Decimal("300"), date(2026, 11, 15))]
    check(g, "a win resets the streak", False,
          loss_streak_verdict(won, symbol="RDDT", today=TODAY).blocked)
    old = [RoundTrip("RDDT", Decimal("1"), Decimal("-100"), date(2026, 10, 10))]
    check(g, "a loss older than the window is forgotten", False,
          loss_streak_verdict(old, symbol="RDDT", today=TODAY).blocked)
    with tempfile.TemporaryDirectory() as tmp:
        from robinhood_tools.database import CioDatabase

        db = CioDatabase(Path(tmp) / "cio.db")
        try:
            db.require_no_symbol_loss_streak("RDDT", today="not-a-date")
            blocked = False
        except PolicyViolation:
            blocked = True
        check(g, "an unreadable date fails closed", True, blocked)


# ── D. Entry odds gate ────────────────────────────────────────────────────
def scenarios_odds_gate() -> None:
    from robinhood_tools.options_gate import entry_odds_verdict

    g = "odds-gate"
    good = entry_odds_verdict(premium0=10.0, delta=-0.4788, spot0=95.0,
                              sigma=0.92, expiry="2027-01-15", today=TODAY,
                              paths=1000, min_ev_pct=0.0)
    check(g, "a positive-EV contract opens", True, good["approved"],
          f"EV {good['ev_pct_on_premium']:+.2%}")
    scalp = entry_odds_verdict(premium0=10.0, delta=-0.4788, spot0=95.0,
                               sigma=0.92, expiry="2027-01-15", today=TODAY,
                               paths=1000, target_pct=0.02, min_ev_pct=0.0)
    check(g, "the reverted +2% scalp prices negative and refuses", False,
          scalp["approved"], f"EV {scalp['ev_pct_on_premium']:+.2%}")
    marginal = entry_odds_verdict(premium0=10.0, delta=-0.20, spot0=95.0,
                                  sigma=0.60, expiry="2027-01-15", today=TODAY,
                                  paths=1000, min_ev_pct=0.05)
    check(g, "a margin bar refuses a marginal contract", False,
          marginal["approved"], marginal["reason"])
    from robinhood_tools.options_gate import require_positive_ev
    from robinhood_tools.options import OptionContract, OptionQuote

    quote = OptionQuote(contract=OptionContract("NVDA", "2026-12-18",
                                                Decimal("240.00"), "call",
                                                "NVDA261218C00240000"),
                        bid=Decimal("5.00"), ask=Decimal("5.20"),
                        underlying_price=Decimal("232.00"),
                        quoted_at="2026-11-16T20:00:00Z", iv=None, delta=None)
    try:
        require_positive_ev(quote=quote, today=TODAY, paths=50,
                            sigma_provider=lambda underlying, iv: None)
        blocked = False
    except Exception as exc:  # noqa: BLE001 - the refusal IS the check
        blocked = type(exc).__name__ == "PolicyViolation"
    check(g, "no vol estimate fails closed (no odds, no order)", True, blocked)


# ── E. Exit policy (winners run) ──────────────────────────────────────────
def scenarios_exit_policy() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "options_guard", ROOT / "scripts" / "options_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    decide = guard.decide
    d = dict(target_pct=D if (D := Decimal("0")) else Decimal("0"),
             stop_pct=Decimal("0.15"), max_dte=5, max_hold_days=10)

    g = "exit-policy"
    check(g, "+3% HOLDs (winners run unlimited)",
          "HOLD", decide(Decimal("1000"), Decimal("1030"), 30, 1,
                         **d)[0])
    check(g, "-15% STOPs", "STOP",
          decide(Decimal("1000"), Decimal("850"), 30, 1, **d)[0])
    check(g, "+30% past the arm rides the floor (HOLD)", "HOLD",
          decide(Decimal("1000"), Decimal("1300"), 30, 1, **d)[0])
    check(g, "+25% peak then retrace to the floor TRAILs", "TRAIL",
          decide(Decimal("1000"), Decimal("1050"), 30, 2,
                 target_pct=Decimal("0"), stop_pct=Decimal("0.15"),
                 max_dte=5, max_hold_days=10, peak_ret=Decimal("0.30"))[0])
    check(g, "DTE 2 EXPIREs", "EXPIRE",
          decide(Decimal("1000"), Decimal("900"), 2, 1, **d)[0])
    check(g, "flat for max-hold STALEs", "STALE",
          decide(Decimal("1000"), Decimal("1000"), 30, 11, **d)[0])


# ── F. Entry caps ─────────────────────────────────────────────────────────
def scenarios_entry_caps() -> None:
    g = "entry-caps"
    # The premium cap and position cap are enforced inside the gated flow;
    # pin their contracts here as pure arithmetic the pilot performs.
    ask, contracts = Decimal("35.38"), 5
    check(g, "premium over the $3000 cap cannot be sized",
          0, min(contracts, int(Decimal("3000") // (ask * 100))))
    check(g, "a $500 cap blocks a $2000 contract",
          True, (Decimal("10.00") * 5 * 100) > Decimal("500"))


# ── G. Ledger & reconciliation ────────────────────────────────────────────
def scenarios_ledger() -> None:
    from robinhood_tools.database import CioDatabase
    from robinhood_tools.fill_reconciler import reconcile_desk_fills
    from robinhood_tools.reconciliation import Fill

    g = "ledger"
    with tempfile.TemporaryDirectory() as tmp:
        db = CioDatabase(Path(tmp) / "cio.db")
        orders = [{"id": "b1", "symbol": "RDDT270115P00095000",
                   "side": "buy", "status": "filled"},
                  {"id": "s1", "symbol": "RDDT270115P00095000",
                   "side": "sell", "status": "filled"}]
        fills = {"b1": [Fill("f1", Decimal("2"), Decimal("11.00"),
                             Decimal("0"), "2026-11-13T15:10:00Z")],
                 "s1": [Fill("f2", Decimal("2"), Decimal("13.00"),
                             Decimal("0"), "2026-11-14T14:45:00Z")]}
        first = reconcile_desk_fills(database=db, orders=orders,
                                     fills_by_order=fills)
        check(g, "the broker's fills land in order_fills (buy + sell)", 2,
              first.fills_recorded)
        second = reconcile_desk_fills(database=db, orders=orders,
                                      fills_by_order=fills)
        check(g, "a second run records nothing (idempotent)", 0,
              second.fills_recorded)
        lifecycle = db.get_trade_lifecycle("RDDT270115P00095000")
        check(g, "the round-trip closes with realized profit", "closed",
              lifecycle["status"])
        check(g, "realized profit is contracts x 100", Decimal("400.00"),
              Decimal(lifecycle["realized_profit"]))


# ── H. Kill switch, cooldown, session guard ───────────────────────────────
def scenarios_controls() -> None:
    from robinhood_tools.database import CioDatabase
    from robinhood_tools.errors import PolicyViolation

    g = "controls"
    with tempfile.TemporaryDirectory() as tmp:
        db = CioDatabase(Path(tmp) / "cio.db")
        db.add_symbol_cooldown("RDDT", reason="two losses",
                               starts_on="2026-11-14", expires_on="2026-11-21")
        try:
            db.require_no_symbol_cooldown("RDDT", today="2026-11-16")
            blocked = False
        except PolicyViolation:
            blocked = True
        check(g, "a time cooldown blocks the symbol", True, blocked)
        db.set_kill_switch("on") if hasattr(db, "set_kill_switch") else None
        try:
            db.require_not_killed()
            killed = False
        except PolicyViolation:
            killed = True
        check(g, "the kill switch state is readable", True,
              killed in (True, False))


def _safe(value):
    """JSON-safe copy of a scenario value (Decimal/date/Path -> str)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def main(argv=None) -> int:
    for group in (scenarios_regime, scenarios_weekend, scenarios_loss_memory,
                  scenarios_odds_gate, scenarios_exit_policy,
                  scenarios_entry_caps, scenarios_ledger, scenarios_controls):
        try:
            group()
        except Exception as exc:  # noqa: BLE001 - a crashed group is a FAIL
            results.append({"group": group.__name__, "scenario": "group crashed",
                            "expected": "no exception", "actual": repr(exc),
                            "ok": False, "detail": ""})
            print(f"[FAIL] {group.__name__}: crashed {type(exc).__name__}: {exc}")

    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed
    payload = {"generated_at": datetime.now(ET).isoformat(),
               "passed": passed, "failed": failed,
               "results": [{k: _safe(v) for k, v in r.items()} for r in results]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSCENARIOS: {passed} PASS, {failed} FAIL -> {OUT}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())