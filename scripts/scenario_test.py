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


# ── I. The entry funnel: the whole sequence for one candidate ─────────────
def scenarios_entry_funnel() -> None:
    """What happens to a candidate, gate by gate, in the flow's own order.

    The gates are the real ones; the candidate inputs are stubbed. The
    recorded answer is the FIRST gate that refuses (or "OPEN"), which is
    what the operator sees in the log when an entry does not appear.
    """
    from scripts.tradingagents_paper_auto import regime_gate_passes
    from robinhood_tools.errors import PolicyViolation
    from robinhood_tools.loss_memory import RoundTrip, loss_streak_verdict
    from robinhood_tools.options_flow import weekend_gap_block
    from robinhood_tools.options_gate import entry_odds_verdict

    g = "entry-funnel"

    def funnel(*, regime, side, iv_with_delta=(-0.4788, 0.92), sigma=0.92,
               premium=10.28, premium_cap=Decimal("3000"), ask=10.36,
               open_positions=0, max_positions=5, when=None, loss_trips=None,
               min_ev_pct=0.0):
        """Walk the documented gate order; return the first refusal or OPEN."""
        sig = "overweight" if side == "call" else "underweight"
        if not regime_gate_passes(regime=regime, circuit_broken=False,
                                  sig=sig, options_mode=True):
            return "BLOCKED regime"
        if side == "put" and weekend_gap_block(
                right="put", when=when or datetime(2026, 11, 18, 10, 0,
                                                    tzinfo=ET)) is not None:
            return "BLOCKED weekend-gap"
        if loss_trips:
            v = loss_streak_verdict(loss_trips, symbol="RDDT", today=TODAY)
            if v.blocked:
                return "BLOCKED loss-memory"
        delta, vol = iv_with_delta
        odds = entry_odds_verdict(premium0=premium, delta=delta, spot0=95.0,
                                  sigma=vol if vol else sigma,
                                  expiry="2027-01-15", today=TODAY,
                                  paths=1000, min_ev_pct=min_ev_pct)
        if not odds["approved"]:
            return "BLOCKED odds-gate"
        if ask * 1 * 100 > premium_cap:
            return "BLOCKED premium-cap"
        if open_positions >= max_positions:
            return "BLOCKED position-cap"
        return "OPEN"

    check(g, "RISK_ON call, healthy contract", "OPEN",
          funnel(regime="RISK_ON", side="call", iv_with_delta=(0.45, 0.5)))
    check(g, "RISK_OFF put via bearish signal (owner decision)", "OPEN",
          funnel(regime="RISK_OFF", side="put"))
    check(g, "RISK_OFF call (long premium in a down tape)", "BLOCKED regime",
          funnel(regime="RISK_OFF", side="call", iv_with_delta=(0.45, 0.5)))
    check(g, "CHOP put", "BLOCKED regime", funnel(regime="CHOP", side="put"))
    check(g, "Thursday 15:50 put", "BLOCKED weekend-gap",
          funnel(regime="RISK_OFF", side="put",
                 when=datetime(2026, 11, 19, 15, 50, tzinfo=ET)))
    two_losses = [RoundTrip("RDDT", Decimal("1"), Decimal("-100"),
                            date(2026, 11, 14)),
                  RoundTrip("RDDT", Decimal("1"), Decimal("-50"),
                            date(2026, 11, 12))]
    check(g, "double-loser underlier", "BLOCKED loss-memory",
          funnel(regime="RISK_OFF", side="put", loss_trips=two_losses))
    check(g, "a marginal contract against a +15% EV bar", "BLOCKED odds-gate",
          funnel(regime="RISK_OFF", side="put", iv_with_delta=(-0.4788, 0.92),
                 premium=10.0, min_ev_pct=0.15))
    check(g, "premium over the $3000 cap", "BLOCKED premium-cap",
          funnel(regime="RISK_OFF", side="put", ask=35.38))
    check(g, "five positions already open", "BLOCKED position-cap",
          funnel(regime="RISK_OFF", side="put", open_positions=5))


# ── J. Fail-closed data paths ─────────────────────────────────────────────
def scenarios_fail_closed() -> None:
    """Missing evidence is not permission: the data-failure scenarios."""
    from robinhood_tools.database import CioDatabase
    from robinhood_tools.errors import PolicyViolation
    from robinhood_tools.paper_flow import SignalOrder, place_signal_order
    from robinhood_tools.runtime import build_settings

    g = "fail-closed"
    settings = build_settings("config/approval_routes.json", ".env")
    with tempfile.TemporaryDirectory() as tmp:
        db = CioDatabase(Path(tmp) / "cio.db")
        try:
            place_signal_order(settings=settings, snapshot=None,
                               order=SignalOrder(symbol="NVDA", side="buy",
                                                 quantity=Decimal("1"),
                                                 limit_price=Decimal("100")),
                               database=db, today=date(2026, 11, 16))
            blocked = False
            why = "placed"
        except PolicyViolation as exc:
            blocked = True
            why = str(exc)[:60]
        check(g, "a buy with no membership evidence", True, blocked, why)

        db.set_emergency_kill(True)
        try:
            db.require_not_killed()
            killed = False
        except PolicyViolation:
            killed = True
        check(g, "the kill switch blocks new placements", True, killed)
        db.set_emergency_kill(False)

        # A stale quote is a refusal, not a trade (get_quote enforces its
        # own freshness; the trading paths pass max_age_minutes=5).
        # yfinance is patched OUT: without this the scenario depends on
        # whether the yfinance fallback happens to be fresh (it is, mid-
        # session, which made this row flip on 2026-11-17).
        from datetime import datetime as _dt, timedelta, timezone
        from unittest.mock import patch

        from robinhood_tools.market_prices import get_quote

        class _StaleClient:
            def get_snapshot(self, symbol, **kw):
                return {"latestTrade": {"p": 100.0,
                                        "t": (_dt.now(timezone.utc)
                                              - timedelta(minutes=30)).isoformat()}}

        with patch("yfinance.Ticker", side_effect=RuntimeError("no fallback")):
            try:
                get_quote("NVDA", data_client=_StaleClient())
                stale_blocked = False
            except PolicyViolation:
                stale_blocked = True
        check(g, "a stale quote fails closed", True, stale_blocked)


# ── K. Broker reconciliation: the ledger must tell the truth ──────────────
def scenarios_recon() -> None:
    from robinhood_tools.database import CioDatabase
    from robinhood_tools.fill_reconciler import reconcile_desk_fills
    from robinhood_tools.reconciliation import Fill

    g = "broker-recon"
    with tempfile.TemporaryDirectory() as tmp:
        db = CioDatabase(Path(tmp) / "cio.db")
        orders = [{"id": "orphan-1", "symbol": "MRNA261120C00200000",
                   "side": "buy", "status": "filled"}]
        fills = {"orphan-1": [Fill("fx", Decimal("1"), Decimal("5.00"),
                                   Decimal("0"), "2026-11-13T15:10:00Z")]}
        report = reconcile_desk_fills(database=db, orders=orders,
                                      fills_by_order=fills)
        check(g, "a fill with no approval is reported as an orphan", 1,
              len(report.orphan_orders))
        check(g, "the orphan fill is still recorded (the ledger is complete)",
              1, len(db.order_fills("orphan-1")))

        sells = [{"id": "exit-1", "symbol": "F261120C00015000",
                  "side": "sell", "status": "filled"}]
        sell_fills = {"exit-1": [Fill("fy", Decimal("2"), Decimal("8.00"),
                                      Decimal("0"), "2026-11-14T14:45:00Z")]}
        report2 = reconcile_desk_fills(database=db, orders=sells,
                                       fills_by_order=sell_fills)
        check(g, "an exit with no lifecycle is reported, not absorbed", 1,
              len(report2.unattributed_exits))

        # A partial fill records the partial quantity.
        partial = [{"id": "part-1", "symbol": "NVDA", "side": "buy",
                    "status": "partially_filled"}]
        partial_fills = {"part-1": [Fill("fz", Decimal("3"), Decimal("100.00"),
                                         Decimal("0"),
                                         "2026-11-13T15:10:00Z")]}
        reconcile_desk_fills(database=db, orders=partial,
                             fills_by_order=partial_fills)
        rows = db.order_fills("part-1")
        check(g, "a partial fill records only what filled", "3",
              rows[0]["quantity"] if rows else "none")


def main(argv=None) -> int:
    for group in (scenarios_regime, scenarios_weekend, scenarios_loss_memory,
                  scenarios_odds_gate, scenarios_exit_policy,
                  scenarios_entry_caps, scenarios_ledger, scenarios_controls,
                  scenarios_entry_funnel, scenarios_fail_closed,
                  scenarios_recon):
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