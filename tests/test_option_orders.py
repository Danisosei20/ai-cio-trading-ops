"""Options order path tests: fingerprint, ledger, guarded placement (sim)."""
from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from robinhood_tools.approvals import option_fingerprint
from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.models import OptionLeg, OptionOrderRequest
from robinhood_tools.risk import RiskLimits
from robinhood_tools.service import RobinhoodTradingService
from robinhood_tools.sim_broker import SimulationBroker
from scripts.options_watch_trade import liquid_signal_filter


def leg(**overrides) -> OptionLeg:
    params = dict(symbol="NVDA261120C00240000", side="buy", effect="open",
                  option_type="call", expiration_date="2026-11-20",
                  strike_price=Decimal("240"), quantity=1)
    params.update(overrides)
    return OptionLeg(**params)


def request(**overrides):
    params = dict(account_id="sim-1", legs=(leg(),), order_type="limit",
                  time_in_force="gfd", limit_price=Decimal("4.10"))
    params.update(overrides)
    return OptionOrderRequest(**params)


class OptionOrderPathTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = CioDatabase(Path(self.temp.name) / "cio.db")
        quote = self._quote()
        self.sim = SimulationBroker(option_quotes={quote.contract.option_symbol: quote})
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        from robinhood_tools.universe import Sp500Snapshot as _Snap

        snap = _Snap(frozenset({"NVDA"}), _dt.now(_tz.utc).isoformat(),
                     "https://example.com/sp500", index_etfs=frozenset({"SPY"}))
        self.service = RobinhoodTradingService(
            self.sim, approval_store=self.db, sp500_snapshot=snap,
            broker_environment="paper", require_human_confirmation=False)

    def tearDown(self):
        self.temp.cleanup()

    def _quote(self):
        from datetime import datetime, timezone

        from robinhood_tools.options import OptionContract, OptionQuote

        contract = OptionContract("NVDA", "2026-11-20", Decimal("240"), "call",
                                  "NVDA261120C00240000")
        return OptionQuote(contract, Decimal("3.80"), Decimal("3.95"), Decimal("237.08"),
                           datetime.now(timezone.utc).isoformat(), delta=Decimal("0.46"),
                           open_interest=2000, volume=500)

    def test_fingerprint_stable_and_tamper_sensitive(self):
        self.assertEqual(option_fingerprint(request()), option_fingerprint(request()))
        changed = request(limit_price=Decimal("4.20"))
        self.assertNotEqual(option_fingerprint(request()), option_fingerprint(changed))

    def test_full_guarded_path(self):
        req = request()
        review = self.service.review_option_order(req)
        auth = self.db.create_option_approval(req, review, window_minutes=120)
        self.assertEqual(auth.status, "pending")
        self.db.approve(auth.approval_id)
        order = self.service.place_option_order(
            req, review_id=review.review_id, approval_id=auth.approval_id, confirmed=False)
        self.assertEqual(order.status, "filled")
        self.assertEqual(self.db.get(auth.approval_id).status, "executed")

    def test_changed_legs_rejected_at_reserve(self):
        req = request()
        review = self.service.review_option_order(req)
        auth = self.db.create_option_approval(req, review, window_minutes=120)
        self.db.approve(auth.approval_id)
        with self.assertRaises(PolicyViolation):
            self.service.place_option_order(
                request(limit_price=Decimal("9.99")), review_id=review.review_id,
                approval_id=auth.approval_id, confirmed=False)

    def test_service_option_universe_gate(self):
        import dataclasses

        from robinhood_tools.options import OptionContract, OptionQuote

        contract = OptionContract("SPY", "2026-11-20", Decimal("680"), "call",
                                  "SPY261120C00680000")
        quote = OptionQuote(contract, Decimal("9.00"), Decimal("9.20"), Decimal("670.00"),
                            "2026-10-07T12:00:00+00:00", delta=Decimal("0.45"),
                            open_interest=5000, volume=900)
        self.sim.update_option_quotes({contract.option_symbol: quote})
        import datetime as _dt

        snap = __import__("robinhood_tools.universe", fromlist=["Sp500Snapshot"]).Sp500Snapshot(
            frozenset({"NVDA"}), _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "https://example.com/sp500", index_etfs=frozenset({"SPY"}))
        self.service.sp500_snapshot = snap
        leg = OptionLeg(symbol=contract.option_symbol, side="buy", effect="open",
                        option_type="call", expiration_date="2026-11-20",
                        strike_price=Decimal("680"), quantity=1)
        req = OptionOrderRequest(account_id="sim-1", legs=(leg,), order_type="limit",
                                 time_in_force="gfd", limit_price=Decimal("9.30"))
        review = self.service.review_option_order(req)
        self.assertIn("alpaca-paper-option-review" if False else "sim-review", review.review_id)
        dia = dataclasses.replace(leg, symbol="DIA261120C00450000")
        with self.assertRaises(PolicyViolation):
            self.service.review_option_order(OptionOrderRequest(
                account_id="sim-1", legs=(dia,), order_type="limit",
                time_in_force="gfd", limit_price=Decimal("9.30")))

    def test_underlying_from_occ(self):
        from robinhood_tools.options import underlying_from_occ

        self.assertEqual(underlying_from_occ("NVDA261120C00240000"), "NVDA")
        self.assertEqual(underlying_from_occ("SPY261120P00685000"), "SPY")
        with self.assertRaises(PolicyViolation):
            underlying_from_occ("NVDA")

    def test_short_not_permitted(self):
        bad = OptionOrderRequest(account_id="sim-1",
                                 legs=(leg(side="sell", effect="open"),),
                                 order_type="limit", time_in_force="gfd",
                                 limit_price=Decimal("4.10"))
        with self.assertRaises(PolicyViolation):
            self.service.review_option_order(bad)
        spread = OptionOrderRequest(account_id="sim-1",
                                    legs=(leg(), leg(symbol="NVDA261120P00240000",
                                                     side="buy", effect="open",
                                                     option_type="put")),
                                    order_type="limit", time_in_force="gfd",
                                    limit_price=Decimal("8.00"))
        with self.assertRaises(PolicyViolation):
            self.service.review_option_order(spread)

    def test_live_environment_refuses_options(self):
        live = RobinhoodTradingService(
            self.sim, approval_store=self.db, sp500_snapshot=None,
            broker_environment="live", require_human_confirmation=True)
        with self.assertRaises(PolicyViolation):
            live.review_option_order(request())
        with self.assertRaises(PolicyViolation):
            live.place_option_order(request(), review_id="x", approval_id="y",
                                    confirmed=True)

    def test_sell_to_close_full_path(self):
        buy = request()
        review = self.service.review_option_order(buy)
        auth = self.db.create_option_approval(buy, review, window_minutes=120)
        self.db.approve(auth.approval_id)
        self.service.place_option_order(
            buy, review_id=review.review_id, approval_id=auth.approval_id,
            confirmed=False)
        sell = OptionOrderRequest(account_id="sim-1",
                                  legs=(leg(side="sell", effect="close"),),
                                  order_type="limit", time_in_force="gfd",
                                  limit_price=Decimal("3.00"))
        review2 = self.service.review_option_order(sell)
        auth2 = self.db.create_option_approval(sell, review2, window_minutes=120)
        self.db.approve(auth2.approval_id)
        order = self.service.place_option_order(
            sell, review_id=review2.review_id, approval_id=auth2.approval_id,
            confirmed=False)
        self.assertEqual(order.status, "filled")

    def test_leg_mutations_rejected(self):
        base = request()
        review = self.service.review_option_order(base)
        auth = self.db.create_option_approval(base, review, window_minutes=120)
        self.db.approve(auth.approval_id)
        mutations = [
            {"side": "sell", "effect": "open"},
            {"strike_price": Decimal("250")},
            {"quantity": 2},
            {"expiration_date": "2026-12-18"},
        ]
        import dataclasses

        for i, change in enumerate(mutations):
            new_leg = dataclasses.replace(base.legs[0], **change)
            mutated = OptionOrderRequest(account_id="sim-1", legs=(new_leg,),
                                         order_type="limit", time_in_force="gfd",
                                         limit_price=Decimal("4.10"))
            with self.subTest(mutation=i):
                with self.assertRaises(PolicyViolation):
                    self.service.place_option_order(
                        mutated, review_id=review.review_id,
                        approval_id=auth.approval_id, confirmed=False)

    def test_direction_and_option_id_bound(self):
        import dataclasses

        base = request()
        review = self.service.review_option_order(base)
        auth = self.db.create_option_approval(base, review, window_minutes=120)
        self.db.approve(auth.approval_id)
        for i, change in enumerate(({"direction": "credit"},
                                     {"option_id": "bogus-id-1"})):
            if "direction" in change:
                mutated = dataclasses.replace(base, **change)
            else:
                new_leg = dataclasses.replace(base.legs[0], **change)
                mutated = OptionOrderRequest(account_id="sim-1", legs=(new_leg,),
                                             order_type="limit", time_in_force="gfd",
                                             limit_price=Decimal("4.10"))
            with self.subTest(mutation=i):
                with self.assertRaises(PolicyViolation):
                    self.service.place_option_order(
                        mutated, review_id=review.review_id,
                        approval_id=auth.approval_id, confirmed=False)

    def test_legacy_store_fails_closed(self):
        import types

        legacy = types.SimpleNamespace()
        service = RobinhoodTradingService(
            self.sim, approval_store=legacy, sp500_snapshot=None,
            broker_environment="paper", require_human_confirmation=False)
        with self.assertRaises(PolicyViolation):
            service.place_option_order(request(), review_id="r", approval_id="a",
                                       confirmed=False)

    def test_double_reserve_rejected(self):
        req = request()
        review = self.service.review_option_order(req)
        auth = self.db.create_option_approval(req, review, window_minutes=120)
        self.db.approve(auth.approval_id)
        self.service.place_option_order(
            req, review_id=review.review_id, approval_id=auth.approval_id,
            confirmed=False)
        with self.assertRaises(PolicyViolation):
            self.service.place_option_order(
                req, review_id=review.review_id, approval_id=auth.approval_id,
                confirmed=False)


class OptionGuardTest(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, ".")
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "options_guard", Path("scripts/options_guard.py"))
        self.guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.guard)

    def guard_default_target(self):
        """The take-profit the guard actually ships with (not a test constant)."""
        argv = self.guard.parse_args([])
        return argv.target_pct

    def test_expire_target_stop_stale_hold(self):
        decide = self.guard.decide
        self.assertEqual(decide(Decimal("1000"), Decimal("900"), 2, 1,
                                Decimal("0.5"), Decimal("0.5"), 2, 5)[0], "EXPIRE")
        self.assertEqual(decide(Decimal("1000"), Decimal("1600"), 30, 1,
                                Decimal("0.5"), Decimal("0.5"), 2, 5)[0], "TARGET")
        self.assertEqual(decide(Decimal("1000"), Decimal("400"), 30, 1,
                                Decimal("0.5"), Decimal("0.5"), 2, 5)[0], "STOP")
        # STALE is for positions that are NOT winning; a loser drifts out.
        self.assertEqual(decide(Decimal("1000"), Decimal("950"), 30, 6,
                                Decimal("0.5"), Decimal("0.5"), 2, 5)[0], "STALE")
        self.assertEqual(decide(Decimal("1000"), Decimal("1100"), 30, 1,
                                Decimal("0.5"), Decimal("0.5"), 2, 5)[0], "HOLD")
        with self.assertRaises(PolicyViolation):
            decide(Decimal("0"), Decimal("100"), 30, 1,
                   Decimal("0.5"), Decimal("0.5"), 2, 5)

    def test_reconciliation_required_close_is_retained_and_skipped_next_cycle(self):
        state = {
            "NVDA261120C00240000": {
                "first_seen": "2026-10-07",
                "cost": "1000",
            }
        }
        result = {
            "status": "filled",
            "order_id": "close-2",
            "reconciliation_required": True,
            "reconciliation": {
                "required": True,
                "reason": "fresh exact-OCC position is non-flat",
                "order_id": "close-2",
                "broker_status": "filled",
                "position_evidence": [{"symbol": "NVDA261120C00240000", "qty": "1"}],
                "claim_released": False,
            },
        }

        self.assertTrue(self.guard.record_close_result(
            state, "NVDA261120C00240000", result))
        self.assertIn("NVDA261120C00240000", state)
        self.assertTrue(state["NVDA261120C00240000"]["reconciliation_required"])
        self.assertEqual(
            state["NVDA261120C00240000"]["reconciliation"]["reason"],
            "fresh exact-OCC position is non-flat",
        )
        self.assertTrue(self.guard.close_reconciliation_pending(
            state["NVDA261120C00240000"]))

        # A subsequent guard cycle must retain recovery metadata and skip a
        # replacement close while the authoritative position is still non-flat.
        class Backend:
            def get_option_order(self, order_id):
                self.order_id = order_id
                return type("Order", (), {"status": "filled"})()

        backend = Backend()
        recovery = self.guard.reconcile_pending_closes(
            state, backend,
            [{"symbol": "NVDA261120C00240000", "qty": "1"}],
        )
        self.assertEqual(recovery[0]["exit_action"], "reconciliation_pending")
        self.assertEqual(backend.order_id, "close-2")
        self.assertIn("NVDA261120C00240000", state)
        self.assertTrue(self.guard.close_reconciliation_pending(
            state["NVDA261120C00240000"]))
        self.assertEqual(state["NVDA261120C00240000"]["reconciliation"]["order_id"], "close-2")

    def test_legacy_flat_state_migrates_without_losing_tracking_metadata(self):
        legacy = {
            "NVDA261120C00240000": {
                "first_seen": "2026-10-07",
                "cost": "1000",
                "pending": {"action": "close", "attempts": 1},
                "reconciliation_required": True,
                "order_id": "close-2",
                "status": "filled",
                "reconciliation": {
                    "reason": "fresh exact-OCC position is non-flat",
                    "claim_released": False,
                },
            }
        }

        state = self.guard.load_tracked_state(legacy)

        self.assertEqual(state, legacy)
        self.assertIsNot(state, legacy)

    def test_new_state_format_is_loaded_as_tracked_records(self):
        tracked = {"NVDA261120C00240000": {"first_seen": "2026-10-07"}}

        state = self.guard.load_tracked_state({"updated": "2026-10-08", "tracked": tracked})

        self.assertIs(state, tracked)
    def test_winners_run_never_closed_by_time(self):
        """With the target explicitly disabled, a winner is not closed by the
        old 2% target or the STALE time exit -- only downside (STOP) and
        expiry (EXPIRE) close it.

        Superseded as the *default* by the 2026-11-06 owner decision to take
        profit at +2%, then restored as the default on 2026-11-16 ("no,
        it should be unlimited"). This pins that target_pct=0 means
        unlimited and that a winner past the +25% arm rides the trail.
        """
        decide = self.guard.decide
        # +180% held 9 days: no target, so no TARGET; in profit, so no STALE.
        act, why = decide(Decimal("1000"), Decimal("2800"), 10, 9,
                          Decimal("0"), Decimal("0.15"), 2, 5)
        self.assertEqual(act, "HOLD")
        self.assertIn("trail armed", why)
        # Small winner on day 1 also holds -- nothing closes it early.
        self.assertEqual(decide(Decimal("1000"), Decimal("1030"), 30, 1,
                                Decimal("0"), Decimal("0.15"), 2, 5)[0], "HOLD")
        # Downside still stops at -15%, winner or not.
        self.assertEqual(decide(Decimal("1000"), Decimal("850"), 30, 1,
                                Decimal("0"), Decimal("0.15"), 2, 5)[0], "STOP")
        # Flat/losing trades still get flushed by STALE.
        self.assertEqual(decide(Decimal("1000"), Decimal("1000"), 30, 6,
                                Decimal("0"), Decimal("0.15"), 2, 5)[0], "STALE")
        # Expiry guard is untouched -- nothing is held into the weekend.
        self.assertEqual(decide(Decimal("1000"), Decimal("2800"), 2, 1,
                                Decimal("0"), Decimal("0.15"), 2, 5)[0], "EXPIRE")

    def test_trailing_stop_protects_big_winners(self):
        """2026-10-17: once peak >= +25%, the stop ratchets up from the peak."""
        decide = self.guard.decide
        D = Decimal
        # Peaked +200% (floor +80%): still above floor -> keep riding.
        self.assertEqual(decide(D("2000"), D("5000"), 30, 2,
                                D("0"), D("0.15"), 2, 5, peak_ret=D("2.0"))[0], "HOLD")
        # Fell back to +70%: below the +80% floor -> trail exit.
        act, why = decide(D("2000"), D("3400"), 30, 2,
                          D("0"), D("0.15"), 2, 5, peak_ret=D("2.0"))
        self.assertEqual(act, "TRAIL")
        self.assertIn("floor", why)
        # Peak under the +25% arm: plain winners-run rules apply.
        self.assertEqual(decide(D("1000"), D("1150"), 30, 2,
                                D("0"), D("0.15"), 2, 5, peak_ret=D("0.20"))[0], "HOLD")
        # A +30% peak arms the trail at a +12% floor.
        self.assertEqual(decide(D("1000"), D("1100"), 30, 2,
                                D("0"), D("0.15"), 2, 5, peak_ret=D("0.30"))[0], "TRAIL")

    def test_the_default_target_lets_winners_run(self):
        """Owner decision 2026-11-16 ("no, it should be unlimited").

        The 2026-11-06 +2% take-profit was reverted: the default target is
        DISABLED, so an ordinary winner is held for the +25% trail instead
        of being scalped. The options Time Machine priced the old policy at
        -$47.53 per contract per round trip (61% win rate at a 1:7.5
        payoff), which is why the default changed.
        """
        decide = self.guard.decide
        act, why = decide(Decimal("1000"), Decimal("1030"), 30, 1,
                          self.guard_default_target(), Decimal("0.15"), 2, 5)
        self.assertEqual(act, "HOLD")
        self.assertIn("winners run", why)
        # The escape hatch remains: an explicit target still closes it.
        act, why = decide(Decimal("1000"), Decimal("1030"), 30, 1,
                          Decimal("0.02"), Decimal("0.15"), 2, 5)
        self.assertEqual(act, "TARGET")
        self.assertIn("3%", why)

    def test_two_percent_target_does_not_close_a_home_run(self):
        """A winner past the +25% trail arm rides the ratcheting floor.

        The ACN call sits above +100%; a flat +2% target would sell it the
        moment it crossed +2%, which is not what "take the 2%" means.
        """
        decide = self.guard.decide
        act, why = decide(Decimal("1000"), Decimal("2200"), 30, 3,
                          self.guard_default_target(), Decimal("0.15"), 2, 5,
                          peak_ret=Decimal("1.2"))
        self.assertEqual(act, "HOLD")
        self.assertIn("trail armed", why)
        # ...and it still exits if it falls back below the locked floor.
        act, why = decide(Decimal("1000"), Decimal("500"), 30, 3,
                          self.guard_default_target(), Decimal("0.15"), 2, 5,
                          peak_ret=Decimal("1.2"))
        self.assertEqual(act, "TRAIL")
        self.assertIn("floor", why)

    def test_guard_default_target_is_disabled(self):
        """Owner 2026-11-16: winners run unlimited — the take-profit default
        is 0 (disabled); the +25% trail floor is the protection."""
        self.assertEqual(self.guard_default_target(), Decimal("0"))

    def test_market_clock_closed_blocks_execution(self):
        """Execution guard must refuse when the Alpaca market clock is closed."""
        from unittest.mock import patch

        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo

        from robinhood_tools.alpaca_paper import AlpacaPaperBackend
        from robinhood_tools.errors import PolicyViolation
        from robinhood_tools.runtime import (_build_paper_service,
                                              PaperAutonomySettings,
                                              RuntimeSettings)
        from robinhood_tools.universe import Sp500Snapshot
        from pathlib import Path

        settings = RuntimeSettings(
            mode="paper_auto", trading_enabled=False, channel_id="C1",
            approval_window_minutes=60,
            database_path=Path(tempfile.mkdtemp()) / "paper.db",
            dashboard_path=Path(tempfile.mkdtemp()) / "dashboard.html",
            risk_limits=RiskLimits(
                max_order_value=Decimal("500"), max_symbol_exposure=Decimal("500"),
                min_cash_dollars=Decimal("50"), max_open_positions=1),
            paper_trading_enabled=True,
            paper_autonomy=PaperAutonomySettings(
                enabled=True, regular_session_only=True,
                earliest_entry_time_et="10:15", latest_entry_time_et="15:45"),
        )
        snapshot = Sp500Snapshot(
            symbols=frozenset({"SPY"}), as_of="2026-01-01T00:00:00Z",
            source_url="https://example.com")

        # Patch the global datetime clock to a Tuesday 11:00 ET (inside the
        # window) so the guard reaches the market-clock check.
        fake_now = _dt(2026, 10, 13, 11, 0, tzinfo=ZoneInfo("America/New_York"))
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.strptime.side_effect = _dt.strptime
            mock_dt.time = _dt.time
            mock_dt.side_effect = _dt

            def run_guard(is_open: bool) -> None:
                class Transport:
                    def request(self, method, path, payload=None):
                        return [{"date": "2026-10-13", "open": "09:30", "close": "16:00"}]

                with patch.object(AlpacaPaperBackend, "market_clock",
                                  return_value={"is_open": is_open}):
                    svc = _build_paper_service(
                        settings=settings, sp500_snapshot=snapshot,
                        earliest="10:15", latest="15:45", transport=Transport(),
                        session_now=fake_now)
                    svc.execution_guard()

            # Market closed -> guard raises.
            with self.assertRaises(PolicyViolation):
                run_guard(False)

            # Market open -> guard passes.
            run_guard(True)  # does not raise



    def test_watchlist_format(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "options_watchlist", Path("scripts/options_watchlist.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rows = [{"ticker": "NVDA", "spot": "237.08", "spot_source": "alpaca-iex",
                 "call": {"contract": "NVDA261120C00240000", "strike": "240",
                          "bid": "10.69", "ask": "10.79", "delta": "0.508",
                          "iv": "0.3422", "premium": "1079.00", "over_cap": False},
                 "put": None}]
        text = mod.format_watchlist(rows, "2026-10-08")
        self.assertIn("*Options watch", text)
        self.assertIn("NVDA261120C00240000", text)
        self.assertIn("no liquid candidate", text)

    def test_occ_details(self):
        from robinhood_tools.options import occ_details

        c = occ_details("NVDA261120C00240000")
        self.assertEqual((c.underlying, c.expiry, c.right, c.strike),
                         ("NVDA", "2026-11-20", "call", Decimal("240")))
        with self.assertRaises(PolicyViolation):
            occ_details("NVDA")

    def test_load_tracked_unwraps_legacy_nesting(self):
        import json
        import tempfile

        # Reproduce the legacy schema that nested the whole prior document
        # under "tracked", which silently reset hold_days to 0 (STALE dead).
        legacy = {
            "updated": "2026-10-13",
            "tracked": {
                "updated": "2026-10-13",
                "tracked": {
                    "MRNA261016C00200000": {"first_seen": "2026-10-08",
                                            "cost": "770"},
                    "NVDA261120C00240000": {"first_seen": "2026-10-08",
                                            "cost": "810"},
                },
                "evaluated": [],
                "AAPL261120C00340000": {"first_seen": "2026-10-09",
                                        "cost": "915"},
                "NVDA261120C00240000": {"first_seen": "2026-10-05",
                                        "cost": "700"},
            },
            "evaluated": [],
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            p.write_text(json.dumps(legacy))
            got = self.guard._load_tracked(p)
        # every symbol found at any depth is recovered, flat
        self.assertEqual(got["MRNA261016C00200000"]["first_seen"], "2026-10-08")
        self.assertEqual(got["AAPL261120C00340000"]["first_seen"], "2026-10-09")
        # duplicates keep the earliest first_seen so hold_days never rewinds
        self.assertEqual(got["NVDA261120C00240000"]["first_seen"], "2026-10-05")

    def test_load_tracked_missing_or_broken_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "nope.json"
            self.assertEqual(self.guard._load_tracked(missing), {})
            broken = Path(d) / "broken.json"
            broken.write_text("{not json")
            self.assertEqual(self.guard._load_tracked(broken), {})


class OptionsWatchTradeTest(unittest.TestCase):
    """15:10 watch -> trade gate (owner 2026-10-14)."""

    def setUp(self):
        import importlib.util
        sys_path = str(Path("scripts/options_watch_trade.py"))
        spec = importlib.util.spec_from_file_location("options_watch_trade", sys_path)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def _screen(self, ranked):
        import json
        import tempfile

        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write(json.dumps({"date": "2026-10-13", "ranked": ranked}))
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return Path(tmp.name)

    def test_gate_skips_overbought_and_held(self):
        path = self._screen([
            {"symbol": "MRNA", "rsi": 66.3, "mom20": 0.50},
            {"symbol": "NTAP", "rsi": 75.2, "mom20": 0.26},   # overbought
            {"symbol": "HPE", "rsi": 59.5, "mom20": -0.02},   # held -> skip
            {"symbol": "EIX", "rsi": 45.2, "mom20": 0.017},
            {"symbol": "MTD", "rsi": 72.0, "mom20": 0.20},
        ])
        got = self.mod.gated_candidates(path, top=5, max_rsi=73.0,
                                        held={"HPE"})
        self.assertEqual(got, [("MRNA", "call"), ("EIX", "call"), ("MTD", "call")])

    def test_direction_follows_momentum_and_stops_at_top(self):
        path = self._screen([
            {"symbol": "AAA", "rsi": 50.0, "mom20": -0.10},  # down -> put
            {"symbol": "BBB", "rsi": 50.0, "mom20": 0.10},   # up -> call
            {"symbol": "CCC", "rsi": 50.0, "mom20": 0.10},
        ])
        self.assertEqual(self.mod.gated_candidates(path, top=2, max_rsi=73.0,
                                                   held=set()),
                         [("AAA", "put"), ("BBB", "call")])

    def test_missing_screen_is_empty_not_crash(self):
        self.assertEqual(
            self.mod.gated_candidates(Path("/no/such/screen.json"), 5, 73.0, set()),
            [])


class MinVolumeOverrideTest(unittest.TestCase):
    """The owner-directed liquidity override, pinned.

    2026-11-06: the desk's own gates refused to re-buy ACN because its option
    chain is thin (most strikes 1-47 contracts against a 100 minimum). Rather
    than lowering the desk default for every path, the pilot takes a scoped
    --min-volume that relaxes ONLY the volume floor - the spread, DTE, delta
    and OI gates are untouched, so a wide spread still blocks.
    """

    def setUp(self):
        import sys
        sys.path.insert(0, ".")
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "options_pilot", Path("scripts/options_pilot.py"))
        self.pilot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pilot)

    def test_default_keeps_the_desk_volume_floor(self):
        argv = self.pilot.parse_args(["--ticker", "ACN"])
        self.assertEqual(argv.min_volume, 0)

    def test_override_relaxes_only_volume(self):
        from robinhood_tools.options import OptionSelectionFilters

        argv = self.pilot.parse_args(
            ["--ticker", "ACN", "--min-volume", "5"])
        self.assertEqual(argv.min_volume, 5)
        filters = OptionSelectionFilters(min_option_volume=argv.min_volume)
        # Volume moved...
        self.assertEqual(filters.min_option_volume, 5)
        # ...and the gates that protect against a bad fill did not.
        self.assertEqual(filters.max_spread_pct, Decimal("0.10"))
        self.assertEqual(filters.min_delta, Decimal("0.30"))
        self.assertEqual(filters.max_delta, Decimal("0.60"))
        self.assertEqual(filters.min_dte, 7)
        self.assertEqual(filters.max_dte, 60)

    def test_spread_gate_still_blocks_under_the_override(self):
        """A thin chain with a wide spread must stay un-buyable."""
        from robinhood_tools.options import OptionContract, OptionQuote, select_long_option

        def q(strike, ask, bid, delta, vol):
            sym = f"ACN261218C{int(round(float(strike) * 1000)):08d}"
            c = OptionContract("ACN", "2026-12-18", Decimal(str(strike)),
                               "call", sym)
            oq = OptionQuote(contract=c, bid=Decimal(str(bid)), ask=Decimal(str(ask)),
                             underlying_price=Decimal("260"),
                             quoted_at="2026-11-06T16:00:00-05:00",
                             delta=Decimal(str(delta)), volume=vol, open_interest=None)
            return oq

        # The real ACN Dec-18 K260: delta in band, volume 6, spread 7.3%.
        # Under the desk default it is rejected on volume alone...
        ok = q(260, "18.86", "17.50", "0.5451", 6)
        with self.assertRaises(Exception):
            select_long_option([ok], direction="bullish",
                               today="2026-11-06")
        # ...and with the override's volume floor it is selected.
        from robinhood_tools.options import OptionSelectionFilters
        f = OptionSelectionFilters(min_option_volume=5)
        selected, rejected = select_long_option(
            [ok], direction="bullish", today="2026-11-06", filters=f)
        self.assertEqual(selected.contract.strike, Decimal("260"))

        # But a wide spread is still rejected even with volume relaxed.
        wide = q(260, "25.00", "17.50", "0.5451", 500)
        with self.assertRaises(Exception):
            select_long_option([wide], direction="bullish",
                               today="2026-11-06", filters=f)


class TestLiquidSignalFilter(unittest.TestCase):
    def _write_signals(self, root, data):
        d = Path(root) / "outputs" / "liquid"
        d.mkdir(parents=True, exist_ok=True)
        (d / "latest.json").write_text(json.dumps(data))

    def test_crowded_long_call_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_signals(root, {
                "signals": {
                    "crowded_longs_smart_money_fading": ["BTCD", "TWST"],
                    "crowded_shorts_smart_money_buying": ["SMH"],
                }})
            path = Path(root) / "outputs" / "liquid" / "latest.json"
            allowed, why = liquid_signal_filter("BTCD", "call", path)
            self.assertFalse(allowed)
            self.assertIn("liquid:", why)
            allowed, why = liquid_signal_filter("BTCD", "put", path)
            self.assertTrue(allowed)

    def test_crowded_short_put_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_signals(root, {
                "signals": {
                    "crowded_longs_smart_money_fading": [],
                    "crowded_shorts_smart_money_buying": ["SMH"],
                }})
            path = Path(root) / "outputs" / "liquid" / "latest.json"
            allowed, why = liquid_signal_filter("SMH", "put", path)
            self.assertFalse(allowed)
            allowed, why = liquid_signal_filter("SMH", "call", path)
            self.assertTrue(allowed)

    def test_clear_symbol_passes(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_signals(root, {
                "signals": {
                    "crowded_longs_smart_money_fading": ["BTCD"],
                    "crowded_shorts_smart_money_buying": ["SMH"],
                }})
            path = Path(root) / "outputs" / "liquid" / "latest.json"
            allowed, why = liquid_signal_filter("PTC", "call", path)
            self.assertTrue(allowed)
            allowed, why = liquid_signal_filter("PTC", "put", path)
            self.assertTrue(allowed)

    def test_missing_file_passes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "outputs" / "liquid" / "latest.json"
            allowed, why = liquid_signal_filter("PTC", "call", path)
            self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
