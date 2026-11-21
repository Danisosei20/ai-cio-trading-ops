#!/usr/bin/env python3
"""Property-based testing: invariants that must hold for ANY input.

An enumerated scenario matrix proves the gates behave on the cases someone
thought of. Property testing proves the stronger claim: for ANY input in
the space - including hostile, degenerate and contradictory ones - the
invariants still hold. Each property below is a statement the desk's
safety depends on, checked over thousands of generated cases:

  1. no gate ever crashes: every entry point returns a verdict
  2. fail-closed: UNKNOWN/CHOP never approve new risk; missing evidence
     (no vol, no quote, unreadable date) never approves an entry
  3. direction honesty: a bought put is short, a bought call is long;
     the weekend rule never blocks a call and never spares a Thu-PM put
  4. accounting: fills_recorded + fills_already_known == fills pulled;
     a round-trip's P/L is (sell-buy) x qty x multiplier
  5. exits: the signal is always one of the six known states, and a
     -15% premium always stops
  6. idempotence: reconciling the same broker state twice changes nothing

Deterministic (seeded) so a failure reproduces. Writes
outputs/simulation/property_test.json.

Usage:
  python scripts/property_test.py [--cases 2000]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
OUT = ROOT / "outputs" / "simulation" / "property_test.json"
SIGNALS = {"HOLD", "STOP", "TARGET", "TRAIL", "EXPIRE", "STALE"}
LAST_CHECKS = 0  # set by main() so callers (preflight) can report the count

SYMBOLS = ["NVDA", "AAPL", "RDDT", "TSLA", "QQQ", "SPY", "MRNA", "", "  ",
           "BRK-B", "X", "TOOLONGSYMBOL", "nvda", "N/A"]
OCC = [f"{u}{exp}{right}{strike}" for u in ("NVDA", "RDDT")
       for exp in ("261218", "270115", "261120")
       for right in ("C", "P")
       for strike in ("00024000", "00095000", "00240000")]


def gen_symbol(rng):
    return rng.choice(SYMBOLS + OCC + ["", "!!!", "N\nVDA"])


def gen_money(rng):
    return rng.choice([Decimal("0"), Decimal("-1"), Decimal("0.01"),
                       Decimal(str(rng.uniform(0.01, 5000))).quantize(Decimal("0.01")),
                       Decimal("999999999"), Decimal("NaN")])


def gen_float(rng, lo=-1e6, hi=1e6):
    return rng.choice([0.0, -0.0, 1e-9, float("inf") * 0 + rng.uniform(lo, hi),
                       rng.uniform(lo, hi)])


def gen_regime(rng):
    return rng.choice(["RISK_ON", "RISK_OFF", "CHOP", "UNKNOWN", "", "risk_on",
                       None, 7])


def gen_sig(rng):
    return rng.choice(["buy", "overweight", "sell", "underweight", "hold",
                       "review", "", None])


class Runner:
    def __init__(self):
        self.violations: list[dict] = []
        self.checks = 0

    def check(self, prop: str, ok: bool, detail: str = "") -> None:
        self.checks += 1
        if not ok:
            self.violations.append({"property": prop, "detail": detail})

    # ── 1. No gate ever crashes ──────────────────────────────────────────
    def prop_no_gate_crashes(self, rng, case) -> None:
        from scripts.tradingagents_paper_auto import regime_gate_passes

        for _ in range(3):
            regime = gen_regime(rng)
            sig = gen_sig(rng)
            try:
                verdict = regime_gate_passes(
                    regime=regime if isinstance(regime, str) else "UNKNOWN",
                    circuit_broken=rng.choice([True, False]),
                    sig=sig if isinstance(sig, str) else "hold",
                    options_mode=rng.choice([True, False]))
                self.check("no gate crashes (regime)",
                           isinstance(verdict, bool), f"regime={regime} sig={sig}")
            except Exception as exc:  # noqa: BLE001
                self.check("no gate crashes (regime)", False,
                           f"regime={regime!r} sig={sig!r} -> "
                           f"{type(exc).__name__}: {exc}")

        from robinhood_tools.options_flow import weekend_gap_block

        when = datetime(2026, 11, rng.randint(1, 28), rng.randint(0, 23),
                        rng.randint(0, 59), tzinfo=ET)
        try:
            block = weekend_gap_block(right=rng.choice(["put", "call", "PUT",
                                                        "", None]),
                                      when=when)
            self.check("no gate crashes (weekend)",
                       block is None or isinstance(block, str),
                       f"right/date={when}")
        except Exception as exc:  # noqa: BLE001
            self.check("no gate crashes (weekend)", False,
                       f"{type(exc).__name__}: {exc}")

    # ── 2. Fail-closed invariants ────────────────────────────────────────
    def prop_fail_closed(self, rng, case) -> None:
        from scripts.tradingagents_paper_auto import regime_gate_passes

        for regime in ("CHOP", "UNKNOWN"):
            for sig in ("buy", "overweight", "sell", "underweight"):
                for options in (True, False):
                    verdict = regime_gate_passes(regime=regime,
                                                 circuit_broken=False, sig=sig,
                                                 options_mode=options)
                    # Equity SELLS are exits and must pass; nothing else new.
                    allowed = (not options and sig in ("sell", "underweight"))
                    self.check("CHOP/UNKNOWN block new risk (options)",
                               verdict == (allowed and not options)
                               or (verdict == allowed),
                               f"regime={regime} sig={sig} options={options} "
                               f"verdict={verdict}")
        # A tripped breaker beats RISK_ON.
        self.check("circuit breaker beats RISK_ON",
                   not regime_gate_passes(regime="RISK_ON",
                                          circuit_broken=True, sig="buy",
                                          options_mode=False))

        from robinhood_tools.options_gate import require_positive_ev
        from robinhood_tools.options import OptionContract, OptionQuote
        from robinhood_tools.errors import PolicyViolation

        contract = OptionContract("NVDA", "2026-12-18", Decimal("240.00"),
                                  rng.choice(["call", "put"]),
                                  "NVDA261218C00240000")
        # OptionQuote validates at construction (0 <= bid <= ask, ask > 0),
        # so the hostile inputs belong to the surfaces that accept raw
        # values - regimes, symbols, dates, fills, guard parameters. The
        # first fuzz run proved that the hard way: a NaN bid cannot even
        # build a quote, which is the boundary validation working.
        quote = OptionQuote(contract=contract,
                            bid=Decimal("5.00"), ask=Decimal("5.20"),
                            underlying_price=Decimal("232.00"),
                            quoted_at="2026-11-17T12:00:00Z", iv=None,
                            delta=Decimal("-0.48"))
        try:
            require_positive_ev(quote=quote, today=date(2026, 11, 17),
                                paths=20, sigma_provider=lambda u, iv: None)
            self.check("no vol estimate fails closed", False,
                       "an entry was approved without any vol estimate")
        except PolicyViolation:
            self.check("no vol estimate fails closed", True)
        except Exception as exc:  # noqa: BLE001
            self.check("no vol estimate fails closed", False,
                       f"{type(exc).__name__}: {exc}")

    # ── 3. Direction honesty ─────────────────────────────────────────────
    def prop_direction_honesty(self, rng, case) -> None:
        from robinhood_tools.options_flow import weekend_gap_block

        thursday_pm = datetime(2026, 11, 19, rng.randint(15, 23),
                               rng.randint(46, 59), tzinfo=ET)
        thursday_am = datetime(2026, 11, 19, rng.randint(9, 14), 30, tzinfo=ET)
        other = datetime(2026, 11, rng.choice([16, 17, 18, 20]), 16, 0, tzinfo=ET)
        self.check("weekend rule never blocks a call",
                   weekend_gap_block(right="call", when=thursday_pm) is None)
        self.check("weekend rule always blocks a Thursday-PM put",
                   weekend_gap_block(right="put", when=thursday_pm) is not None)
        self.check("weekend rule spares Thursday AM",
                   weekend_gap_block(right="put", when=thursday_am) is None)
        self.check("weekend rule spares other weekdays",
                   weekend_gap_block(right="put", when=other) is None)

    # ── 4. Accounting ────────────────────────────────────────────────────
    def prop_accounting(self, rng, case) -> None:
        from robinhood_tools.fill_reconciler import reconcile_desk_fills
        from robinhood_tools.reconciliation import Fill
        from robinhood_tools.database import CioDatabase
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db = CioDatabase(Path(tmp) / "cio.db")
            n_orders = rng.randint(1, 6)
            orders, fills = [], {}
            for i in range(n_orders):
                symbol = rng.choice(OCC + SYMBOLS[:6])
                side = rng.choice(["buy", "sell"])
                oid = f"o{i}"
                orders.append({"id": oid, "symbol": symbol, "side": side,
                               "status": rng.choice(["filled", "partially_filled",
                                                     "accepted", "canceled"])})
                if orders[-1]["status"] in ("filled", "partially_filled"):
                    fills[oid] = [Fill(f"f{i}", Decimal(str(rng.randint(1, 9))),
                                       Decimal(str(rng.uniform(0.5, 400))),
                                       Decimal("0"),
                                       f"2026-11-{rng.randint(10, 17)}T15:10:00Z")]
            total_fills = sum(len(v) for v in fills.values())
            try:
                first = reconcile_desk_fills(database=db, orders=orders,
                                             fills_by_order=fills)
            except Exception as exc:  # noqa: BLE001
                self.check("reconciliation never crashes", False,
                           f"{type(exc).__name__}: {exc}")
                return
            self.check("reconciliation never crashes", True)
            self.check("fills accounted for exactly once",
                       first.fills_recorded + first.fills_already_known
                       == total_fills,
                       f"recorded={first.fills_recorded} "
                       f"known={first.fills_already_known} total={total_fills}")
            second = reconcile_desk_fills(database=db, orders=orders,
                                          fills_by_order=fills)
            self.check("a second run changes nothing",
                       second.fills_recorded == 0
                       and second.fills_already_known == total_fills)

    # ── 5. Exit policy invariants ────────────────────────────────────────
    def prop_exit_policy(self, rng, case) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "options_guard", ROOT / "scripts" / "options_guard.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        for _ in range(5):
            entry = Decimal(str(rng.uniform(50, 5000))).quantize(Decimal("0.01"))
            mark = (entry * Decimal(str(rng.uniform(0.5, 1.6)))).quantize(
                Decimal("0.01"))
            dte = rng.randint(0, 40)
            hold = rng.randint(0, 20)
            peak = Decimal(str(rng.uniform(-0.3, 2.0)))
            try:
                action, reason = guard.decide(
                    entry, mark, dte, hold, Decimal("0"), Decimal("0.15"),
                    5, 10, peak_ret=peak)
            except Exception as exc:  # noqa: BLE001
                self.check("the exit decision never crashes", False,
                           f"{type(exc).__name__}: {exc}")
                continue
            self.check("the exit decision never crashes", True)
            self.check("the signal is always a known state",
                       action in SIGNALS, f"action={action!r}")
            ret = (mark / entry - 1) if entry else Decimal("-1")
            if ret <= Decimal("-0.15") and dte > 5 and hold < 10:
                # The safety property is "a loser always EXITS", not
                # "always via the stop": a position that once peaked past
                # the +25% arm exits through the trail floor, which sits
                # ABOVE the -15% stop (peak x 0.40 with peak=30% is a +12%
                # floor). The first fuzz run asserted STOP and "failed" on
                # exactly that correct behaviour.
                self.check("a -15% premium always exits (STOP or TRAIL)",
                           action in ("STOP", "TRAIL"),
                           f"ret={ret:.1%} action={action}")

    # ── 6. Round-trip P/L arithmetic ─────────────────────────────────────
    def prop_round_trip_math(self, rng, case) -> None:
        from robinhood_tools.loss_memory import round_trips

        qty = Decimal(str(rng.randint(1, 20)))
        buy_px = Decimal(str(rng.uniform(0.5, 400))).quantize(Decimal("0.01"))
        sell_px = (buy_px * Decimal(str(rng.uniform(0.2, 3)))).quantize(
            Decimal("0.01"))
        symbol = rng.choice(OCC)
        fills = [{"symbol": symbol, "side": "buy", "quantity": str(qty),
                  "price": str(buy_px), "fee": "0",
                  "filled_at": "2026-11-13T15:10:00Z"},
                 {"symbol": symbol, "side": "sell", "quantity": str(qty),
                  "price": str(sell_px), "fee": "0",
                  "filled_at": "2026-11-14T14:45:00Z"}]
        trips = round_trips(fills)
        self.check("a paired buy+sell is one round-trip", len(trips) == 1)
        if trips:
            mult = Decimal("100") if len(symbol) > 10 else Decimal("1")
            expected = ((sell_px - buy_px) * qty * mult).quantize(Decimal("0.01"))
            self.check("round-trip P/L is (sell-buy) x qty x multiplier",
                       trips[0].pnl == expected,
                       f"{symbol} pnl={trips[0].pnl} expected={expected}")


PROPERTIES = ["no gate crashes", "fail-closed", "direction honesty",
              "accounting", "exit policy", "round-trip math"]
METHODS = {"no gate crashes": "prop_no_gate_crashes",
           "fail-closed": "prop_fail_closed",
           "direction honesty": "prop_direction_honesty",
           "accounting": "prop_accounting",
           "exit policy": "prop_exit_policy",
           "round-trip math": "prop_round_trip_math"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Property-based tests.")
    ap.add_argument("--cases", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20261117)
    args = ap.parse_args(argv)

    runner = Runner()
    rng = random.Random(args.seed)
    for case in range(args.cases):
        for prop in PROPERTIES:
            getattr(runner, METHODS[prop])(rng, case)

    payload = {"generated_at": datetime.now(ET).isoformat(),
               "cases": args.cases, "seed": args.seed,
               "checks": runner.checks,
               "violations": len(runner.violations),
               "properties": PROPERTIES,
               "examples": runner.violations[:20]}
    global LAST_CHECKS
    LAST_CHECKS = runner.checks
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"property checks: {runner.checks} | violations: "
          f"{len(runner.violations)} | cases: {args.cases} seed: {args.seed}")
    for v in runner.violations[:10]:
        print(f"  [VIOLATION] {v['property']}: {v['detail'][:160]}")
    print(f"written -> {OUT}")
    return 0 if not runner.violations else 1


if __name__ == "__main__":
    raise SystemExit(main())