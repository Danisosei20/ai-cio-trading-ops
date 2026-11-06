#!/usr/bin/env python3
"""Paper-execution certification harness (repeatable, offline-first).

Proves the desk's execution paths behave correctly *before* live sessions
depend on them. Every scenario is deterministic and isolated:

  * risk-controller state is redirected to a throw-away temp dir, so the
    real kill switch / exposure ledger is never touched;
  * order placement is stubbed - this harness can never send an order;
  * the only network calls are read-only broker reads (account, positions),
    and they are skipped with --no-broker or on any failure.

Scenario groups map to the owner's certification checklist:

  1. entry   - bullish long, bearish short, bearish-on-existing-long exit
  2. gates   - odds fail-closed, odds-against, direction-aware zone gate,
               missing quote / no risk distance, a zero-priced short names
               its quote cause, RISK_OFF offers puts / UNKNOWN fails closed
  3. exits   - options guard STOP / TARGET / TRAIL / EXPIRE / STALE,
               day-trade flatten at 15:45
  4. risk    - kill switch, duplicate, per-book exposure cap (long and short
               counted separately), a long and a short in one symbol are two
               rows, a dry-run entry charges nothing (the cooldown still
               applies), an entry is charged at the fill and its exit releases
               it, position cap, daily drawdown, stale data, release on sell
  5. safety  - order timeout returns a block (never hangs), quote error is
               mapped to a clean block, broker is paper-only
  6. truth   - broker reconciliation (orphan exposure) and dashboard P/L
               versus broker P/L

Every check that can reach the risk ledger runs inside `isolated_risk()` --
and that is every *short* scenario, because a short's sizing reads the short
book's remaining budget. A short check left outside it reads the real ledger:
it passes only while the desk happens to be flat, and false-FAILs the moment
the short book is in use.

Usage:
  python scripts/paper_certification.py            # full run
  python scripts/paper_certification.py --no-broker  # offline only
  python scripts/paper_certification.py --json out.json --md out.md

Exit code 0 = no FAIL (WARN/SKIP allowed), 1 = at least one FAIL.
Paper only. Never places an order.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import types
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
EQUITY = Decimal("100000")

PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"


@dataclass
class Result:
    group: str
    name: str
    status: str
    detail: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"group": self.group, "name": self.name,
                "status": self.status, "detail": self.detail,
                "evidence": self.evidence}


class Recorder:
    def __init__(self) -> None:
        self.results: list[Result] = []

    def add(self, group: str, name: str, status: str, detail: str,
            **evidence) -> Result:
        r = Result(group, name, status, detail, evidence)
        self.results.append(r)
        return r

    def check(self, group: str, name: str, ok: bool, detail: str,
              **evidence) -> Result:
        return self.add(group, name, PASS if ok else FAIL, detail, **evidence)

    def counts(self) -> dict:
        out = {PASS: 0, FAIL: 0, SKIP: 0, WARN: 0}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out


# ────────────────────────────────────────────────────────────────────────────
# Isolated risk controller
# ────────────────────────────────────────────────────────────────────────────

@contextmanager
def isolated_risk():
    """Point the risk controller's module globals at a temp dir.

    `_acquire_lock`, `check_kill_switch` and the default `state_path` all read
    module-level Paths at call time, so patching the module attributes is
    enough to keep the real ledger untouched.
    """
    import robinhood_tools.portfolio_risk as pr

    saved = (pr.STATE_FILE, pr.KILL_SWITCH_FILE, pr.LOCK_FILE)
    tmp = Path(tempfile.mkdtemp(prefix="paper-cert-risk-"))
    pr.STATE_FILE = tmp / "state.json"
    pr.KILL_SWITCH_FILE = tmp / "KILL_SWITCH"
    pr.LOCK_FILE = tmp / "risk.lock"
    try:
        yield pr, tmp
    finally:
        pr.STATE_FILE, pr.KILL_SWITCH_FILE, pr.LOCK_FILE = saved


# ────────────────────────────────────────────────────────────────────────────
# Fake order modules (proves the order path fails cleanly, never places)
# ────────────────────────────────────────────────────────────────────────────

def _fake_order_modules(raise_on_place: Exception | None):
    """Build sys.modules replacements for the five modules _place_order imports."""

    paper_flow = types.ModuleType("robinhood_tools.paper_flow")

    class SignalOrder:  # noqa: D401 - test double
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def place_signal_order(**kwargs):
        if raise_on_place is not None:
            raise raise_on_place
        return {"status": "accepted", "order_id": "fake-order-1"}

    paper_flow.SignalOrder = SignalOrder
    paper_flow.place_signal_order = place_signal_order

    runtime = types.ModuleType("robinhood_tools.runtime")
    runtime.build_settings = lambda *a, **k: types.SimpleNamespace(
        index_etf_arbitrage=(), database_path=":memory:")
    runtime.build_paper_service_with_session = lambda **k: object()

    settings_mod = types.ModuleType("robinhood_tools.settings")
    settings_mod.load_env = lambda *a, **k: {}

    database_mod = types.ModuleType("robinhood_tools.database")

    class CioDatabase:
        def __init__(self, *a, **k):
            pass

        def record_candidate(self, *a, **k):
            pass

        def record_opinion(self, *a, **k):
            pass

        def update_candidate_status(self, *a, **k):
            pass

    database_mod.CioDatabase = CioDatabase

    alpaca = types.ModuleType("robinhood_tools.alpaca_paper")

    class _Transport:
        @classmethod
        def from_values(cls, values):
            return cls()

        def request(self, method, path, payload=None):
            return {"equity": str(EQUITY), "account_number": "PA-FAKE"}

    class AlpacaPaperBackend:
        def __init__(self, transport):
            self.transport = transport

    alpaca.AlpacaPaperHttpTransport = _Transport
    alpaca.AlpacaPaperBackend = AlpacaPaperBackend

    return {
        "robinhood_tools.paper_flow": paper_flow,
        "robinhood_tools.runtime": runtime,
        "robinhood_tools.settings": settings_mod,
        "robinhood_tools.database": database_mod,
        "robinhood_tools.alpaca_paper": alpaca,
    }


@contextmanager
def fake_order_path(raise_on_place: Exception | None = None):
    mods = _fake_order_modules(raise_on_place)
    saved = {name: sys.modules.get(name) for name in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@contextmanager
def patched_attrs(module, **values):
    """Temporarily replace attributes on a live module; restored on exit."""
    saved = {name: getattr(module, name, None) for name in values}
    for name, value in values.items():
        setattr(module, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


@contextmanager
def fake_zone_analysis():
    """Stub the watch's zone confluence gate so no symbol reaches yfinance.

    `main()` calls `zone_analysis.analyze(symbol)` per surviving candidate and
    that function fetches bars, which the offline harness must not do. It is
    fail-open by construction (the caller wraps it in `except Exception:
    pass`), so a neutral verdict keeps the run hermetic without changing
    which candidates are *offered* -- the regime routing under test happens
    before this gate.
    """
    fake = types.ModuleType("scripts.zone_analysis")
    fake.analyze = lambda symbol, *a, **k: {"verdict": {"verdict": "neutral"}}
    saved = sys.modules.get("scripts.zone_analysis")
    sys.modules["scripts.zone_analysis"] = fake
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("scripts.zone_analysis", None)
        else:
            sys.modules["scripts.zone_analysis"] = saved


def _watch_entries(regime: str, *, weak: list[dict], ranked: list[dict],
                   held: set[str]) -> str:
    """Drive the 15:10 watch's entry selection offline; return its stdout.

    `main()` is called rather than the pure helpers because the behaviour
    worth certifying is the wiring: the regime has to choose the feed, pin
    the side, and keep the held / RSI gates on the way. Every network edge
    is closed on the way in -- `run` (the subprocess that would reach
    options_pilot) returns the pilot's dry-run payload and nothing else,
    `held_underliers` never touches the broker, `_regime` never fetches SPY,
    and the zone gate is stubbed above.
    """
    import scripts.options_watch_trade as owt

    def _dry_run_pilot(cmd):
        # The payload options_pilot.py prints in --dry-run. It is data, not a
        # broker call: this harness never places an order.
        return 0, json.dumps({"ticker": "CERTPUT", "action": "dry_run_buy"}), ""

    out = io.StringIO()
    with tempfile.TemporaryDirectory(prefix="paper-cert-watch-") as d:
        weak_path = Path(d) / "weak.json"
        weak_path.write_text(json.dumps({"date": "2026-11-05", "weak": weak}),
                             encoding="utf-8")
        screen_path = Path(d) / "latest.json"
        screen_path.write_text(json.dumps({"date": "2026-11-05",
                                           "ranked": ranked}),
                               encoding="utf-8")
        argv = ["--no-exits", "--no-notify",
                "--screen", str(screen_path), "--weak-screen", str(weak_path)]
        with fake_zone_analysis(), \
                patched_attrs(owt, run=_dry_run_pilot,
                              held_underliers=lambda env_file: held,
                              _regime=lambda: regime), \
                redirect_stdout(out):
            owt.main(argv)
    return out.getvalue()


def _watch_entry_line(stdout: str) -> str:
    """The watch's `[entry] ... candidates=[...]` line, for exact assertions."""
    return next((ln for ln in stdout.splitlines()
                 if ln.startswith("[entry]")), "")


@contextmanager
def fake_bars(price: float):
    """A yfinance whose every ticker's last 5m close is `price`.

    The desk venv has no yfinance, so `manage_exits`'s `import yfinance` would
    fail and the price fetch would be skipped. Injecting the module is what
    lets an exit scenario be driven offline at all.
    """
    import types

    class _Close:
        def __init__(self, values):
            self._values = values

        @property
        def iloc(self):
            values = self._values

            class _Iloc:
                def __getitem__(self, i):
                    return values[i]

            return _Iloc()

    class _Frame:
        empty = False

        def __init__(self):
            self._close = _Close([price])

        def __getitem__(self, key):
            return self._close

    mod = types.ModuleType("yfinance")

    class _Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kw):
            return _Frame()

    mod.Ticker = _Ticker
    saved = sys.modules.get("yfinance")
    sys.modules["yfinance"] = mod
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = saved


@contextmanager
def engine_cycle(dry_run: bool, setups: list[dict]):
    """Drive one day-trade engine cycle offline; yield the engine module.

    Everything the cycle can write is redirected: the risk ledger
    (`isolated_risk`), the trade state machine, the engine's output root (the
    scout file it reads back), its log, and the two wall-clock gates -- a cycle
    run after 15:15 ET must still be able to enter, or the check would pass
    only in the morning. The broker is `fake_order_path`, so no order can
    leave the process.
    """
    import robinhood_tools.trade_state as trade_state
    import scripts.daytrade_scout as scout
    from scripts import daytrade_engine as de

    with isolated_risk(), fake_order_path(), \
            tempfile.TemporaryDirectory(prefix="paper-cert-engine-") as d:
        # `_exit_position` reads the backend off the engine module while
        # `_place_order` imports it again, so both need the stub above. Read
        # it out of sys.modules: `import a.b as c` binds the package
        # attribute, which fake_order_path() does not replace.
        fake_alpaca = sys.modules["robinhood_tools.alpaca_paper"]

        def _scout_main(argv=None):
            path = de.ROOT_PATH / "outputs" / "daytrade" / \
                f"{datetime.now(ET).date().isoformat()}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"setups": setups}), encoding="utf-8")
            return 0

        with patched_attrs(trade_state,
                           STATE_FILE=Path(d) / "trade_state.json"), \
                patched_attrs(de, ROOT_PATH=Path(d),
                              AlpacaPaperBackend=fake_alpaca.AlpacaPaperBackend,
                              AlpacaPaperHttpTransport=fake_alpaca.AlpacaPaperHttpTransport,
                              _load_equity=lambda: EQUITY,
                              _is_no_new_entries_time=lambda now=None: False,
                              _is_flatten_time=lambda now=None: False,
                              _log=lambda msg: None), \
                patched_attrs(scout, main=_scout_main):
            yield de


# ────────────────────────────────────────────────────────────────────────────
# Scenario groups
# ────────────────────────────────────────────────────────────────────────────

def _long_rec(**over) -> dict:
    rec = {
        "ticker": "CERTL",
        "setup": "long_reclaim",
        "price": 100.0,
        "or_low": 99.0,
        "or_high": 101.0,
        "vwap": 99.5,
        "zone_verdict": "long_ok",
        "odds": {"available": True, "target_first": 0.55, "stop_first": 0.30},
    }
    rec.update(over)
    return rec


def _short_rec(**over) -> dict:
    rec = {
        "ticker": "CERTS",
        "setup": "short_breakdown",
        "price": 100.0,
        "or_low": 99.0,
        "or_high": 101.0,
        "vwap": 100.5,
        "zone_verdict": "short_ok",
        "odds": {"available": False, "reason": "time machine long-side only"},
    }
    rec.update(over)
    return rec


def group_entries(rec: Recorder) -> None:
    from scripts.daytrade_engine import _validate_setup

    ok, reason, plan = _validate_setup(_long_rec(), EQUITY)
    rec.check(
        "entry", "bullish long entry approved",
        ok and plan.get("direction") == "long"
        and plan["stop"] < plan["entry"] < plan["target"]
        and plan["qty"] > 0 and plan["r_r_ratio"] >= 2.0,
        f"{reason} | {plan.get('direction')} entry={plan.get('entry')} "
        f"stop={plan.get('stop')} target={plan.get('target')} qty={plan.get('qty')}",
        plan=plan,
    )

    # A long needs no budget, so it cannot reach the ledger. A short does:
    # _validate_setup builds a PortfolioRiskController() to size the entry
    # against the short book's remaining budget. Left outside isolated_risk()
    # that controller reads the *real* ledger, so this check passes only while
    # the desk is flat -- under live short exposure it is rejected for
    # "short notional budget exhausted" and the certification false-FAILs
    # exactly when the short book is doing its job.
    with isolated_risk():
        ok, reason, plan = _validate_setup(_short_rec(), EQUITY)
        rec.check(
            "entry", "bearish short entry approved",
            ok and plan.get("direction") == "short"
            and plan["stop"] > plan["entry"] > plan["target"]
            and plan["qty"] > 0,
            f"{reason} | {plan.get('direction')} entry={plan.get('entry')} "
            f"stop={plan.get('stop')} target={plan.get('target')} qty={plan.get('qty')}",
            plan=plan,
        )

    # sell-to-close returns before the budget read, but it is a _short_rec
    # scenario: keep it inside the same isolation so a reordering inside
    # _validate_setup cannot silently re-open the hole above.
    with isolated_risk():
        ok, reason, plan = _validate_setup(
            _short_rec(), EQUITY,
            open_positions={"CERTS": {"qty": 10, "direction": "long"}}
        )
        rec.check(
            "entry", "bearish signal on existing long routes to sell-to-close",
            ok and plan.get("direction") == "sell" and plan.get("qty") == 10
            and plan.get("stop") is None,
            f"{reason} | direction={plan.get('direction')} qty={plan.get('qty')}",
            plan=plan,
        )


def group_gates(rec: Recorder) -> None:
    from scripts.daytrade_engine import _validate_setup

    ok, reason, _ = _validate_setup(
        _long_rec(odds={"error": True, "reason": "time machine timeout"}), EQUITY
    )
    rec.check("gates", "odds failure is fail-closed (blocks entry)",
              not ok and "time machine unavailable" in reason, reason)

    ok, reason, _ = _validate_setup(
        _long_rec(odds={"available": True, "target_first": 0.10, "stop_first": 0.20}),
        EQUITY,
    )
    rec.check("gates", "target_first < 20% blocks entry",
              not ok and "target_first" in reason, reason)

    ok, reason, _ = _validate_setup(
        _long_rec(odds={"available": True, "target_first": 0.50, "stop_first": 0.45}),
        EQUITY,
    )
    rec.check("gates", "stop_first >= 40% blocks entry",
              not ok and "stop_first" in reason, reason)

    ok, reason, _ = _validate_setup(
        _long_rec(zone_verdict="long_blocked"), EQUITY
    )
    rec.check("gates", "long_blocked blocks a long",
              not ok and "zone blocked" in reason, reason)

    ok, reason, _ = _validate_setup(
        _long_rec(zone_verdict="short_blocked"), EQUITY
    )
    rec.check("gates", "short_blocked does not block a long (direction-aware)",
              ok, reason)

    # The two short scenarios below build a PortfolioRiskController() to read
    # the short book's budget, so they touch the ledger and must be isolated:
    # under live short exposure an un-isolated check reports "short notional
    # budget exhausted" -- a budget reason -- instead of the gate reason it
    # exists to prove, and the certification false-FAILs while the desk is
    # simply short.
    with isolated_risk():
        ok, reason, _ = _validate_setup(
            _short_rec(zone_verdict="short_blocked"), EQUITY
        )
        rec.check("gates", "short_blocked blocks a short",
                  not ok and "zone blocked" in reason, reason)

    with isolated_risk():
        ok, reason, _ = _validate_setup(
            _short_rec(zone_verdict="long_blocked"), EQUITY
        )
        rec.check("gates", "long_blocked does not block a short (direction-aware)",
                  ok, reason)

    ok, reason, _ = _validate_setup(
        _long_rec(price=0.0, or_low=0.0, or_high=0.0, vwap=0.0), EQUITY
    )
    rec.check("gates", "missing/zero quote is blocked (no risk distance)",
              not ok and "risk distance" in reason, reason)

    # Task 4's cause distinction, beside the zero-quote check: a short whose
    # entry is zero has *no quote*, which triages to the data feed, not to the
    # ledger. Both fail closed, but "budget exhausted" sends an operator to
    # chase exposure for a bad price.
    with isolated_risk():
        ok, reason, _ = _validate_setup(
            _short_rec(price=0.0, or_high=1.0), EQUITY
        )
        rec.check("gates", "a zero-priced short reports a quote cause, not a budget cause",
                  not ok and "short entry price invalid" in reason
                  and "budget" not in reason, reason)

    ok, reason, _ = _validate_setup(_long_rec(setup="no_such_setup"), EQUITY)
    rec.check("gates", "unknown setup is rejected",
              not ok and "not a tradeable setup" in reason, reason)

    # ── Task 5: the 15:10 watch offers puts and calls under RISK_OFF ──────
    # Driving main() rather than the pure helpers, because what matters is the
    # wiring: the regime has to add the weak feed as puts, keep the ranked feed
    # as its own momentum, and still run the held filter and the RSI cap on the
    # way through.
    WEAK_TAIL = [{"symbol": "WEAK1", "mom20": -0.30, "rsi": 40.0}]
    RANKED = [{"symbol": "STRONG1", "mom20": 0.20, "rsi": 50.0}]

    out = _watch_entries("RISK_OFF", weak=WEAK_TAIL, ranked=RANKED, held=set())
    line = _watch_entry_line(out)
    rec.check("gates", "RISK_OFF offers the weak tail as puts and the ranked feed as calls",
              "regime=RISK_OFF" in line and "feed=weak+ranked" in line
              and "candidates=[('WEAK1', 'put'), ('STRONG1', 'call')]" in line
              and "would place WEAK1 put (dry-run)" in out,
              line or "no [entry] line")

    out = _watch_entries(
        "RISK_OFF",
        weak=[{"symbol": "WEAK1", "mom20": -0.30, "rsi": 40.0},
              {"symbol": "WEAK2", "mom20": -0.20, "rsi": 90.0},
              {"symbol": "WEAK3", "mom20": -0.10, "rsi": 45.0}],
        ranked=RANKED, held={"WEAK1"})
    line = _watch_entry_line(out)
    # WEAK1 is held (no doubling a name) and WEAK2 is over the RSI cap; only
    # the clean survivor is offered - and it is offered from BOTH feeds, so
    # neither feed bypasses the filters. The held set is echoed in the same
    # line, so the filter demonstrably had something to drop.
    rec.check("gates", "RISK_OFF still applies the held filter and the RSI cap",
              "candidates=[('WEAK3', 'put'), ('STRONG1', 'call')]" in line
              and "held=['WEAK1']" in line,
              line or "no [entry] line")

    # Missing evidence is not permission: UNKNOWN must not manufacture put
    # candidates. It falls back to the ranked feed, which is strength, so the
    # weak tail's WEAK1 is never offered.
    out = _watch_entries("UNKNOWN", weak=WEAK_TAIL, ranked=RANKED, held=set())
    line = _watch_entry_line(out)
    rec.check("gates", "UNKNOWN regime fails closed (no put candidate; ranked feed used)",
              "regime=UNKNOWN" in line and "feed=ranked" in line
              and "candidates=[('STRONG1', 'call')]" in line
              and "WEAK1" not in line,
              line or "no [entry] line")

    out = _watch_entries(
        "RISK_ON",
        weak=WEAK_TAIL,
        ranked=[{"symbol": "UP1", "mom20": 0.20, "rsi": 40.0},
                {"symbol": "DN1", "mom20": -0.20, "rsi": 40.0}],
        held=set())
    line = _watch_entry_line(out)
    rec.check("gates", "RISK_ON leaves the per-row direction rule untouched",
              "regime=RISK_ON" in line and "feed=ranked" in line
              and "candidates=[('UP1', 'call'), ('DN1', 'put')]" in line,
              line or "no [entry] line")

    # A provider that cannot be imported must read UNKNOWN, never a silent
    # RISK_ON default: that is how a down tape keeps buying calls.
    import scripts.options_watch_trade as owt

    saved_yf = sys.modules.get("yfinance")
    sys.modules["yfinance"] = None  # makes `import yfinance` raise ImportError
    try:
        closes = owt._spy_closes()
        regime = owt._regime()
    finally:
        if saved_yf is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = saved_yf
    rec.check("gates", "a missing quote provider fails closed (never RISK_ON)",
              closes == [] and regime == "UNKNOWN",
              f"_spy_closes()={closes} _regime()={regime}")


def group_exits(rec: Recorder) -> None:
    from scripts.options_guard import decide

    D = Decimal
    common = dict(target_pct=D("0"), stop_pct=D("0.15"),
                  max_dte=5, max_hold_days=10)

    action, reason = decide(D("1.00"), D("0.80"), dte=30, hold_days=1, **common)
    rec.check("exits", "options -20% triggers STOP", action == "STOP", f"{action}: {reason}")

    action, reason = decide(D("1.00"), D("0.85"), dte=30, hold_days=1, **common)
    rec.check("exits", "options -15% boundary triggers STOP", action == "STOP",
              f"{action}: {reason}")

    action, reason = decide(D("1.00"), D("3.00"), dte=30, hold_days=1, **common)
    rec.check("exits", "options +200% is HOLD (profit unlimited, trail armed)",
              action == "HOLD", f"{action}: {reason}")

    action, reason = decide(D("1.00"), D("1.60"), dte=30, hold_days=1,
                            peak_ret=D("3.00"), **common)
    rec.check("exits", "options peak +300% with ret +60% hits TRAIL floor",
              action == "TRAIL", f"{action}: {reason}")

    action, reason = decide(D("1.00"), D("0.50"), dte=3, hold_days=1, **common)
    rec.check("exits", "options DTE <= max triggers EXPIRE", action == "EXPIRE",
              f"{action}: {reason}")

    action, reason = decide(D("1.00"), D("0.99"), dte=30, hold_days=10,
                            target_pct=D("0.50"), stop_pct=D("0.15"),
                            max_dte=5, max_hold_days=10)
    rec.check("exits", "options flat + max hold triggers STALE", action == "STALE",
              f"{action}: {reason}")

    # Owner decision 2026-11-06: take profit at +2%. An ordinary winner (peak
    # under the +25% trail arm) is closed by the default target.
    action, reason = decide(D("1.00"), D("1.03"), dte=30, hold_days=1,
                            target_pct=D("0.02"), stop_pct=D("0.15"),
                            max_dte=5, max_hold_days=10)
    rec.check("exits", "options +3% with the +2% default triggers TARGET",
              action == "TARGET", f"{action}: {reason}")

    # ...but a home run past the +25% arm is NOT sold at +2%: the ratcheting
    # floor protects it. A flat target would have closed the ACN call at +110%.
    action, reason = decide(D("1.00"), D("1.60"), dte=30, hold_days=1,
                            target_pct=D("0.50"), stop_pct=D("0.15"),
                            max_dte=5, max_hold_days=10)
    rec.check("exits", "options +60% past the trail arm rides the floor, not the target",
              action == "HOLD" and "trail armed" in reason,
              f"{action}: {reason}")

    # The boundary is the arm, not the size of the profit.
    action, reason = decide(D("1.00"), D("1.20"), dte=30, hold_days=1,
                            target_pct=D("0.02"), stop_pct=D("0.15"),
                            max_dte=5, max_hold_days=10)
    rec.check("exits", "options +20% (below the arm) with +50% target triggers TARGET",
              action == "TARGET", f"{action}: {reason}")

    from scripts.daytrade_engine import _is_flatten_time
    at_1544 = datetime(2026, 11, 5, 15, 44, tzinfo=ET)
    at_1545 = datetime(2026, 11, 5, 15, 45, tzinfo=ET)
    rec.check("exits", "day trades are not flattened at 15:44 ET",
              _is_flatten_time(at_1544) is False, "15:44 -> False")
    rec.check("exits", "day trades are flattened at 15:45 ET",
              _is_flatten_time(at_1545) is True, "15:45 -> True")

    # 15:45 flatten must not touch overnight positions.
    import scripts.flatten_daytrades as fd
    saved_dir = fd.AUTO_DIR
    tmp = Path(tempfile.mkdtemp(prefix="paper-cert-flat-"))
    try:
        fd.AUTO_DIR = tmp
        for ticker, style, action in (
            ("CERTD", "day_trade", "buy_placed"),
            ("CERTO", "overnight", "overnight_placed"),
        ):
            d = tmp / ticker / "2026-11-05"
            d.mkdir(parents=True)
            (d / "auto.json").write_text(json.dumps(
                {"ticker": ticker, "trade_style": style, "action": action}),
                encoding="utf-8")
        records = fd._today_records("2026-11-05")
        symbols = {r["ticker"] for r in records}
    finally:
        fd.AUTO_DIR = saved_dir

    rec.check("exits", "15:45 flatten targets day trades only (overnight isolated)",
              symbols == {"CERTD"}, f"flatten records: {sorted(symbols)}",
              day_trade_records=sorted(symbols))


def group_risk(rec: Recorder) -> None:
    # Each check gets its own isolated ledger. Sharing one controller would let
    # an earlier check (e.g. filling all 5 position slots) mask the reason a
    # later check blocks, which would certify the wrong behaviour.
    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        v = ctrl.check("CERTSYM", "buy", Decimal("500"), "intraday")
        rec.check("risk", "clean buy is approved", v.approved, v.reason,
                  checks=v.checks)

    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        ctrl.record_order("CERTSYM", "buy", "intraday", Decimal("500"))
        v = ctrl.check("CERTSYM", "buy", Decimal("500"), "intraday")
        rec.check("risk", "duplicate buy inside cooldown is blocked",
                  not v.approved and "duplicate" in v.reason, v.reason)

    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        v = ctrl.check("CERTBIG", "buy", Decimal("6000"), "intraday")
        rec.check("risk", "notional above the $5,000 exposure cap is blocked",
                  not v.approved and "exposure" in v.reason, v.reason)

    # Task 4 sizes a short against the short book's remaining budget, so the
    # per-book cap is the gate a short actually dies at. The first three prove
    # the cap is real and charged to the long book; the rest prove the two
    # books are separate ledgers rather than one shared $5,000.
    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        v = ctrl.check("CERTL5K", "buy", Decimal("5000"), "intraday")
        rec.check("risk", "clean $5,000 long is approved", v.approved, v.reason,
                  checks=v.checks)

    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        v = ctrl.check("CERTL5K", "buy", Decimal("5001"), "intraday")
        rec.check("risk", "$5,001 long is blocked (per-book cap reached)",
                  not v.approved and "exposure" in v.reason, v.reason,
                  checks=v.checks)

    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        ctrl.record_order("CERTHELD", "buy", "intraday", Decimal("5000"))
        v = ctrl.check("CERTL5K", "sell", Decimal("5000"), "intraday", short=True)
        # One $5,000 long fills the long book without touching the 5-position
        # limit, so nothing but a shared cap could reject this short.
        rec.check("risk", "a $5,000 short is approved while a $5,000 long is "
                  "already open (books are independent)",
                  v.approved, v.reason, checks=v.checks)

    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        ctrl.record_order("CERTHELD", "buy", "intraday", Decimal("5000"))
        ctrl.record_order("CERTSHORT", "sell", "intraday", Decimal("5000"),
                          short=True)
        v = ctrl.check("CERTL5K", "sell", Decimal("5001"), "intraday", short=True)
        rec.check("risk", "$5,001 short is blocked by the short cap while the "
                  "long book is full",
                  not v.approved and "short exposure" in v.reason, v.reason,
                  checks=v.checks)

    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        ctrl.record_order("CERTBOOK", "sell", "intraday", Decimal("2500"),
                          short=True)
        book = ctrl.state.book
        # A short `sell` that lands in the long book would release exposure it
        # never held and leave the short book reading zero while short.
        rec.check("risk", "a short entry lands in book['short'] and leaves "
                  "book['long'] empty",
                  "CERTBOOK" in book["short"] and not book["long"],
                  f"long={sorted(book['long'])} short={sorted(book['short'])}",
                  long=sorted(book["long"]), short=sorted(book["short"]))

    with isolated_risk() as (pr, tmp):
        # A ledger written before direction was tracked has no `book` key and
        # its rows carry no `direction`; it must still load and still count as
        # long exposure, or an upgrade mid-session silently re-opens risk.
        (tmp / "state.json").write_text(json.dumps({
            "open_positions": {
                "CERTLEG": {"strategy": "intraday", "notional": "1000",
                            "entered_at": "x"}
            }}), encoding="utf-8")
        ctrl = pr.PortfolioRiskController()
        v = ctrl.check("CERTL5K", "sell", Decimal("4000"), "intraday", short=True)
        rec.check("risk", "a legacy state file with no 'book' key loads and "
                  "still tracks the long",
                  v.approved and "CERTLEG" in ctrl.state.book["long"]
                  and not ctrl.state.book["short"],
                  f"long={sorted(ctrl.state.book['long'])} "
                  f"short={sorted(ctrl.state.book['short'])}")

    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        for i in range(5):
            ctrl.record_order(f"CERT{i}", "buy", "intraday", Decimal("100"))
        v = ctrl.check("CERTNEW", "buy", Decimal("100"), "intraday")
        rec.check("risk", "6th open position is blocked (max 5)",
                  not v.approved and "max open positions" in v.reason, v.reason)

    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        v = ctrl.check("CERTSTALE", "buy", Decimal("100"), "intraday",
                       data_age_sec=300)
        rec.check("risk", "stale quote (>120s) is blocked",
                  not v.approved and "stale data" in v.reason, v.reason)

    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        # The first equity reading seeds peak_equity, so the baseline must be
        # established before a drop can be measured against it.
        ctrl.check("CERTBASE", "buy", Decimal("100"), "intraday",
                   current_equity=100000.0)
        v = ctrl.check("CERTDD", "buy", Decimal("100"), "intraday",
                       current_equity=96000.0)
        rec.check("risk", "4% daily drawdown is blocked",
                  not v.approved and "drawdown" in v.reason, v.reason)

    # A restart must not reset the breaker: peak_equity is persisted, so a
    # mid-drawdown process restart cannot silently re-open risk.
    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        ctrl.check("CERTBASE2", "buy", Decimal("100"), "intraday",
                   current_equity=100000.0)
        reloaded = pr.PortfolioRiskController()
        v = reloaded.check("CERTDD2", "buy", Decimal("100"), "intraday",
                           current_equity=96000.0)
        rec.check("risk", "drawdown breaker survives a restart (peak persisted)",
                  not v.approved and "drawdown" in v.reason, v.reason)

    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        ctrl.record_order("CERTSYM", "buy", "intraday", Decimal("500"))
        ctrl.record_order("CERTSYM", "sell", "intraday", Decimal("500"))
        open_now = ctrl.status()["open_positions"]
        rec.check("risk", "sell releases exposure (symbol no longer tracked)",
                  "CERTSYM" not in open_now and "long:CERTSYM" not in open_now,
                  f"open={sorted(open_now)}")

    # A short entry on a symbol the ledger still carries as a long must not
    # un-book the long: one dict keyed by symbol alone silently dropped the
    # long row, so the pre-existing long exposure stopped being bounded by
    # its own cap -- the one place this work failed open instead of closed.
    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        ctrl.record_order("CERTBOTH", "buy", "intraday", Decimal("4000"))
        ctrl.record_order("CERTBOTH", "sell", "intraday", Decimal("2000"),
                          short=True)
        expo = ctrl.status()["exposure"]
        rec.check("risk", "a short on a symbol carried as long keeps the long "
                  "booked (both books are counted)",
                  "CERTBOTH" in ctrl.state.book["long"]
                  and "CERTBOTH" in ctrl.state.book["short"]
                  and expo["long_notional"] == 4000
                  and expo["short_notional"] == 2000,
                  f"long={sorted(ctrl.state.book['long'])} "
                  f"short={sorted(ctrl.state.book['short'])}",
                  long_notional=expo["long_notional"],
                  short_notional=expo["short_notional"])

    # Finding A: the ledger is charged by fills, never by attempts. The desk
    # runs dry_run=True, so a charge recorded before the dry-run branch is a
    # permanent phantom short -- nothing releases it, and the short book then
    # blocks every later short with "short notional budget exhausted".
    with isolated_risk() as (pr, _tmp):
        from scripts.daytrade_engine import _call_risk_controller
        ok, reason = _call_risk_controller(
            {"ticker": "CERTDRY", "direction": "short", "qty": 50.0,
             "entry": 100.0}, EQUITY)
        ctrl = pr.PortfolioRiskController()
        noted = [(o["symbol"], o["side"]) for o in ctrl.state.recent_orders]
        rec.check("risk", "a dry-run short entry is approved, charged nothing, "
                  "and still cools down",
                  ok and not ctrl.state.book["short"]
                  and not ctrl.state.book["long"]
                  and ("CERTDRY", "sell") in noted,
                  f"{reason} | long={sorted(ctrl.state.book['long'])} "
                  f"short={sorted(ctrl.state.book['short'])} noted={noted}",
                  short_notional=ctrl.status()["exposure"]["short_notional"],
                  recent_orders=noted)

    # ... and the engine-level half of that property, which the final review
    # recorded as unproven: the entry is charged when the order reaches the
    # broker, and its exit releases the book it was charged to.
    with engine_cycle(dry_run=False, setups=[_short_rec(ticker="CERTENG")]) as de:
        import robinhood_tools.portfolio_risk as pr
        result = de.run_cycle(dry_run=False)
        charged = pr.PortfolioRiskController().state.book["short"]
        exposure = pr.PortfolioRiskController().status()["exposure"]
        with fake_bars(97.0):  # below the 98 target: the exit fires
            exits = de.manage_exits(dry_run=False)
        released = pr.PortfolioRiskController().state.book["short"]
    rec.check("risk", "an entry is charged at the fill and its exit releases it",
              len(result["entries"]) == 1 and "CERTENG" in charged
              and exposure["short_notional"] == 5000.0
              and len(exits["exits"]) == 1 and not released,
              f"entries={len(result['entries'])} "
              f"short={sorted(charged)} "
              f"short_notional={exposure['short_notional']} "
              f"exits={len(exits['exits'])} released={sorted(released)}",
              short_notional=exposure["short_notional"],
              exits=len(exits["exits"]))

    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        ctrl.engage_kill_switch("certification")
        v = ctrl.check("CERTKILL", "buy", Decimal("100"), "intraday")
        rec.check("risk", "kill switch blocks all new buys",
                  not v.approved and "kill switch" in v.reason, v.reason)
        ctrl.clear_kill_switch()

    # Restart recovery: state persisted and reloaded by a second controller.
    with isolated_risk() as (pr, _tmp):
        ctrl = pr.PortfolioRiskController()
        ctrl.record_order("CERTPERSIST", "buy", "intraday", Decimal("250"))
        reloaded = pr.PortfolioRiskController()
        rec.check("risk", "risk state survives a restart (persisted + reloaded)",
                  "long:CERTPERSIST" in reloaded.state.open_positions,
                  f"reloaded open={sorted(reloaded.state.open_positions)}")


def group_safety(rec: Recorder) -> None:
    import scripts.daytrade_engine as de

    # 1. A hanging order placement must return a block, not hang the cycle.
    saved = de._place_order
    try:
        def _hang(plan):
            import time
            time.sleep(5)
            return True, "never reached", "never"

        de._place_order = _hang
        started = datetime.now()
        ok, reason, oid = de._place_order_with_timeout({"ticker": "CERTHANG"}, 1)
        elapsed = (datetime.now() - started).total_seconds()
    finally:
        de._place_order = saved

    rec.check("safety", "order timeout returns a block (bounded, never hangs)",
              not ok and "timeout" in reason and "unconfirmed" in reason
              and elapsed < 3.0,
              f"{reason} after {elapsed:.1f}s")

    # 2. A quote failure is mapped to a clean block, never raised.
    plan = {"ticker": "CERTQ", "direction": "long", "setup": "long_reclaim",
            "entry": 100.0, "stop": 99.0, "target": 102.0, "qty": 10,
            "r_r_ratio": 2.0, "zone_verdict": "long_ok"}
    with fake_order_path(raise_on_place=RuntimeError("403 quote unavailable")):
        ok, reason, oid = de._place_order(plan)
    rec.check("safety", "quote error becomes a clean block (no exception)",
              not ok and reason.startswith("quote/data error"),
              reason)

    # 3. A non-quote failure is reported as an order error, still no raise.
    with fake_order_path(raise_on_place=ValueError("bad bracket")):
        ok, reason, oid = de._place_order(plan)
    rec.check("safety", "broker rejection becomes an order-error block",
              not ok and reason.startswith("order error"), reason)

    # 4. The happy path still returns the broker's order id.
    with fake_order_path():
        ok, reason, oid = de._place_order(plan)
    rec.check("safety", "stubbed happy path returns an order id",
              ok and oid == "fake-order-1", f"{reason} id={oid}")


def group_broker_truth(rec: Recorder, enabled: bool) -> None:
    if not enabled:
        rec.add("truth", "broker reconciliation", SKIP, "--no-broker")
        rec.add("truth", "dashboard P/L versus broker", SKIP, "--no-broker")
        return

    try:
        import os
        from robinhood_tools.alpaca_paper import (AlpacaPaperBackend,
                                                  AlpacaPaperHttpTransport)
        from robinhood_tools.settings import load_env

        env = {**load_env(".env"), **os.environ}
        transport = AlpacaPaperHttpTransport.from_values(env)
        backend = AlpacaPaperBackend(transport)
        account = backend.transport.request("GET", "/v2/account")
        positions = backend.list_positions()
    except Exception as exc:  # noqa: BLE001 - broker down must not fail cert
        detail = f"broker read failed: {type(exc).__name__}: {exc}"
        rec.add("truth", "broker reconciliation", SKIP, detail)
        rec.add("truth", "dashboard P/L versus broker", SKIP, detail)
        return

    base_url = getattr(transport, "base_url", None) or getattr(
        transport, "_base_url", None)
    paper_only = base_url is None or "paper-api.alpaca.markets" in str(base_url)
    rec.check("truth", "broker endpoint is Alpaca paper",
              paper_only, f"base_url={base_url}")

    broker_symbols = {str(p.get("symbol", "")).upper() for p in positions
                      if Decimal(str(p.get("qty", "0"))) != 0}

    # Orphan exposure: risk controller thinks we hold something the broker
    # does not. Stale exposure silently consumes the $5,000 cap.
    try:
        from robinhood_tools.portfolio_risk import PortfolioRiskController
        # open_positions is keyed "book:symbol" so a long and a short in one
        # symbol are two rows; the broker only knows symbols.
        tracked = {key.split(":", 1)[1]
                   for key in PortfolioRiskController().status()["open_positions"]}
    except Exception as exc:  # noqa: BLE001
        tracked = set()
        rec.add("truth", "risk ledger readable", WARN,
                f"{type(exc).__name__}: {exc}")

    orphans = sorted(tracked - broker_symbols)
    rec.add(
        "truth", "no orphan exposure in the risk ledger",
        PASS if not orphans else WARN,
        "risk ledger matches broker" if not orphans
        else f"tracked but not at broker: {orphans}",
        tracked=sorted(tracked), broker=sorted(broker_symbols),
    )

    # Dashboard P/L versus broker P/L (equity positions only).
    try:
        from scripts import trading_state
        dash = trading_state.state()
        dash_positions = dash.get("positions", [])
        dash_pl = sum(float(p.get("unrealized_pl", 0) or 0)
                      for p in dash_positions)
    except Exception as exc:  # noqa: BLE001
        rec.add("truth", "dashboard P/L versus broker", SKIP,
                f"trading_state failed: {type(exc).__name__}: {exc}")
        return

    broker_pl = sum(float(p.get("unrealized_pl", 0) or 0) for p in positions)
    gross = sum(abs(float(p.get("market_value", 0) or 0)) for p in positions)
    tolerance = max(1.0, 0.005 * gross)
    delta = abs(dash_pl - broker_pl)
    rec.check(
        "truth", "dashboard P/L matches broker within tolerance",
        delta <= tolerance,
        f"dashboard={dash_pl:.2f} broker={broker_pl:.2f} "
        f"delta={delta:.2f} tolerance={tolerance:.2f}",
        dashboard_pl=round(dash_pl, 2), broker_pl=round(broker_pl, 2),
        positions=len(positions), equity=account.get("equity"),
    )


# ────────────────────────────────────────────────────────────────────────────
# Report
# ────────────────────────────────────────────────────────────────────────────

def _render_markdown(payload: dict) -> str:
    c = payload["counts"]
    lines = [
        "# Paper Execution Certification",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- Verdict: **{payload['verdict']}**",
        f"- Checks: {c.get(PASS, 0)} PASS / {c.get(FAIL, 0)} FAIL / "
        f"{c.get(WARN, 0)} WARN / {c.get(SKIP, 0)} SKIP",
        "- Paper only. No orders were placed by this harness.",
        "",
        "| Group | Check | Status | Detail |",
        "|---|---|---|---|",
    ]
    for r in payload["results"]:
        detail = str(r["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r['group']} | {r['name']} | {r['status']} | {detail} |")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Paper execution certification.")
    p.add_argument("--json", default=str(ROOT / "outputs" / "certification"
                                         / "paper_certification.json"))
    p.add_argument("--md", default=str(ROOT / "outputs" / "certification"
                                       / "paper_certification.md"))
    p.add_argument("--no-broker", action="store_true",
                   help="skip read-only broker reconciliation")
    args = p.parse_args(argv)

    rec = Recorder()
    for group, fn in (
        ("entry", group_entries),
        ("gates", group_gates),
        ("exits", group_exits),
        ("risk", group_risk),
        ("safety", group_safety),
    ):
        try:
            fn(rec)
        except Exception as exc:  # noqa: BLE001 - one group must not kill the run
            rec.add(group, f"{group} group crashed", FAIL,
                    f"{type(exc).__name__}: {exc}")

    try:
        group_broker_truth(rec, enabled=not args.no_broker)
    except Exception as exc:  # noqa: BLE001
        rec.add("truth", "broker truth group crashed", FAIL,
                f"{type(exc).__name__}: {exc}")

    counts = rec.counts()
    verdict = "FAIL" if counts.get(FAIL) else "PASS"
    payload = {
        "generated_at": datetime.now(ET).isoformat(),
        "verdict": verdict,
        "counts": counts,
        "results": [r.as_dict() for r in rec.results],
    }

    json_path = Path(args.json)
    md_path = Path(args.md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")

    # ASCII-only console output (Windows cp1252).
    for r in rec.results:
        print(f"[{r.status}] {r.group}: {r.name} - {r.detail}")
    print(f"\n{verdict}: {counts.get(PASS, 0)} PASS, {counts.get(FAIL, 0)} FAIL, "
          f"{counts.get(WARN, 0)} WARN, {counts.get(SKIP, 0)} SKIP")
    print(f"json: {json_path}")
    print(f"md:   {md_path}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
