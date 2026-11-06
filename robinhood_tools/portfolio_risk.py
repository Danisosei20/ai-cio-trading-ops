#!/usr/bin/env python3
"""Central Portfolio Risk Controller — single gate between all strategies and Alpaca.

Every strategy (equities, day trades, options, overnight) must pass through
this controller before any order reaches Alpaca. It enforces:

  - Direction-aware exposure limits: the long book and the short book are
    separate ledgers, each capped on its own, so a short no longer dies at a
    cap the long book is already using
  - Position count limits (max open positions)
  - Drawdown limits (max daily/weekly loss)
  - Duplicate order protection (same symbol+side within cooldown window)
  - Stale data detection (market data older than threshold → block)
  - Global kill switch (file-based, instant halt)
  - Restart recovery (state persisted to disk, reloaded on startup)

Direction: without `direction`/`short` a call behaves exactly as it always
has — `buy` opens a long and `sell` releases it. With `short=True` (or
`direction="short"`) the buy/sell meaning inverts: a `sell` opens the short
book and a `buy` releases it. A long `sell` that releases a held position is
never booked as a short entry.

Usage:
  # As a Python module:
  from robinhood_tools.portfolio_risk import PortfolioRiskController
  ctrl = PortfolioRiskController(settings, env)
  verdict = ctrl.check(symbol="NVDA", side="buy", notional=500, strategy="overnight")
  if verdict.approved:
      place_order(...)
  else:
      log(f"REJECTED: {verdict.reason}")

  # As a CLI gate (exit 0 = approved, exit 1 = rejected):
  python scripts/portfolio_risk_check.py --symbol NVDA --side buy --notional 500 --strategy overnight
  python scripts/portfolio_risk_check.py --symbol NVDA --side sell --notional 500 \\
      --strategy intraday --short          # short entry, charged to the short book
  python scripts/portfolio_risk_check.py --symbol NVDA --side sell --notional 500 \\
      --strategy intraday --direction short   # equivalent spelling
"""
from __future__ import annotations

# import fcntl  # not available on Windows; using file-based lock instead
import json
import os
import sys
from dataclasses import dataclass, field, asdict
import time as _time

# ... (existing imports)


def _acquire_lock(timeout: float = 10.0):
    """Acquire a file-based lock (Windows-compatible). Returns fd."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    deadline = _time.time() + timeout
    while True:
        try:
            fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT | os.O_EXCL)
            return fd
        except (IOError, OSError):
            if _time.time() > deadline:
                raise TimeoutError("could not acquire portfolio risk lock")
            _time.sleep(0.05)


def _release_lock(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
STATE_FILE = ROOT / "outputs" / "paper" / "portfolio_risk_state.json"
KILL_SWITCH_FILE = ROOT / "outputs" / "paper" / "KILL_SWITCH"
LOCK_FILE = ROOT / "outputs" / "paper" / "portfolio_risk.lock"


@dataclass
class RiskVerdict:
    approved: bool
    reason: str
    checks: dict = field(default_factory=dict)


@dataclass
class RiskState:
    """Persisted state for restart recovery."""
    last_updated: str = ""
    open_positions: dict = field(default_factory=dict)  # "book:symbol" -> {strategy, notional, entered_at, direction}
    # Same symbols as open_positions, grouped by direction:
    # {"long": {symbol: {...}}, "short": {symbol: {...}}}. Derived data —
    # open_positions stays the source of truth; _regroup() rebuilds this.
    book: dict = field(default_factory=lambda: {"long": {}, "short": {}})
    recent_orders: list = field(default_factory=list)     # [{symbol, side, ts, strategy}]
    daily_pnl_baseline: float = 0.0
    peak_equity: float = 0.0
    kill_switch: bool = False

    def _regroup(self) -> None:
        """Rebuild `book` from open_positions' stored direction.

        A row with no `direction` — a ledger written before direction was
        tracked — is long, which is what the old behaviour assumed. Never
        saves; the controller saves after the call.

        `open_positions` is keyed `"{book}:{symbol}"` (see `_position_key`), so
        a long and a short in the same symbol are two rows and neither can
        silently un-book the other. A pre-split ledger keys its rows by the
        bare symbol; those rows are longs, and rewriting them to the long key
        here is what stops the next `record_order` on that symbol from writing
        a second row and double-counting the notional.
        """
        long_book: dict = {}
        short_book: dict = {}
        normalized: dict = {}
        for key, pos in (self.open_positions or {}).items():
            book = "short" if pos.get("direction") == "short" else "long"
            # A key already carrying this book's prefix belongs to it; anything
            # else (a legacy bare symbol) is re-keyed to its own book.
            symbol = key[len(book) + 1:] if key.startswith(f"{book}:") else key
            normalized[f"{book}:{symbol}"] = pos
            (short_book if book == "short" else long_book)[symbol] = pos
        self.open_positions = normalized
        self.book = {"long": long_book, "short": short_book}


class PortfolioRiskController:
    """Single deterministic gate between all strategies and Alpaca."""

    def __init__(self, settings=None, env=None, state_path: Path | None = None):
        self.settings = settings
        self.env = env or {}
        self.state_path = state_path or STATE_FILE
        self.state = self._load_state()
        self._duplicate_cooldown_sec = 300  # 5 min cooldown between same symbol+side
        self._stale_data_threshold_sec = 120  # market data older than 2 min = stale
        self._max_total_notional = Decimal("5000")  # account-level cap; the per-book cap below is the gate
        self._max_book_notional = Decimal("5000")  # per book: long, and short, counted separately
        self._max_open_positions = 5
        self._max_daily_loss_pct = 0.03  # 3% daily loss limit
        self._max_weekly_loss_pct = 0.07  # 7% weekly loss limit

    def _book_notional(self, book: str) -> Decimal:
        """Open notional in one book ("long" or "short")."""
        return sum((Decimal(str(v.get("notional", 0)))
                    for v in self.state.book.get(book, {}).values()), Decimal("0"))

    @staticmethod
    def _is_short(direction: str | None, short: bool | None) -> bool:
        """Resolve which book an order touches. Absent direction = long."""
        if short is not None:
            return bool(short)
        return direction == "short"

    @staticmethod
    def _position_key(symbol: str, short: bool) -> str:
        """`open_positions` key for one book's row in this symbol.

        The book is part of the key, not just a field on the row: otherwise a
        short entry on a symbol the ledger still carries as a long overwrites
        the long row, the long book silently reads zero, and its cap stops
        bounding anything.
        """
        return f"{'short' if short else 'long'}:{symbol}"

    def _load_state(self) -> RiskState:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                state = RiskState(**{k: v for k, v in raw.items()
                                     if k in RiskState.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError, OSError):
                return RiskState()
        else:
            return RiskState()
        # A ledger written before direction existed has no `book` and its rows
        # carry no `direction`, so regroup them as longs.
        state._regroup()
        return state

    def _save_state(self) -> None:
        self.state.last_updated = datetime.now(ET).isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(asdict(self.state), indent=2, default=str),
                                   encoding="utf-8")

    def check_kill_switch(self) -> bool:
        """Check if the global kill switch is engaged."""
        if KILL_SWITCH_FILE.exists():
            self.state.kill_switch = True
            self._save_state()
            return True
        if self.state.kill_switch:
            return True
        return False

    def engage_kill_switch(self, reason: str = "manual") -> None:
        """Engage the global kill switch. All new orders blocked until cleared."""
        KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        KILL_SWITCH_FILE.write_text(f"engaged: {datetime.now(ET).isoformat()}\nreason: {reason}\n",
                                   encoding="utf-8")
        self.state.kill_switch = True
        self._save_state()

    def clear_kill_switch(self) -> None:
        """Clear the global kill switch."""
        if KILL_SWITCH_FILE.exists():
            KILL_SWITCH_FILE.unlink()
        self.state.kill_switch = False
        self._save_state()

    def check_duplicate(self, symbol: str, side: str, strategy: str) -> bool:
        """True if a duplicate order was placed recently (within cooldown)."""
        now = datetime.now(ET)
        cutoff = now - timedelta(seconds=self._duplicate_cooldown_sec)
        for order in self.state.recent_orders:
            try:
                order_ts = datetime.fromisoformat(order["ts"])
                if order_ts > cutoff and order["symbol"] == symbol and order["side"] == side:
                    return True
            except (ValueError, KeyError):
                continue
        return False

    def check_exposure(self, symbol: str, notional: Decimal,
                       short: bool = False) -> tuple[bool, str]:
        """Check if adding this position would exceed its book's exposure cap.

        Long and short notional are counted separately: each book is capped at
        `_max_book_notional`, so a short entry is invisible to the long book
        (and vice versa) instead of being charged a budget that is already
        tracking something else.
        """
        book = "short" if short else "long"
        total = self._book_notional(book)
        if total + notional > self._max_book_notional:
            label = "short exposure" if short else "total exposure"
            return False, f"{label} {total}+{notional} > {self._max_book_notional}"
        # The 5-position limit is shared across both books, so a symbol held in
        # either one is already counted and a repeat order is not a new slot.
        held = (set(self.state.book.get("long", {}))
                | set(self.state.book.get("short", {})))
        if len(self.state.open_positions) >= self._max_open_positions and symbol not in held:
            return False, f"max open positions ({self._max_open_positions}) reached"
        return True, "ok"

    def check_drawdown(self, current_equity: float) -> tuple[bool, str]:
        """Check if daily/weekly drawdown limits are breached."""
        if self.state.peak_equity == 0:
            self.state.peak_equity = current_equity
            self._save_state()
            return True, "ok"
        if current_equity > self.state.peak_equity:
            self.state.peak_equity = current_equity
            self._save_state()
        dd = (self.state.peak_equity - current_equity) / self.state.peak_equity
        if dd > self._max_daily_loss_pct:
            return False, f"daily drawdown {dd:.1%} > {self._max_daily_loss_pct:.0%}"
        return True, "ok"

    def check_stale_data(self, data_age_sec: float) -> tuple[bool, str]:
        """Block if market data is stale."""
        if data_age_sec > self._stale_data_threshold_sec:
            return False, f"stale data: {data_age_sec:.0f}s > {self._stale_data_threshold_sec}s threshold"
        return True, "ok"

    def check(self, symbol: str, side: str, notional: Decimal | float,
             strategy: str, data_age_sec: float = 0,
             current_equity: float = 0, direction: str | None = None,
             short: bool | None = None) -> RiskVerdict:
        """Single entry point. Returns approved/rejected with reasons.

        `direction`/`short` say which book this order touches. With no
        direction the call is a long order, exactly as before: a `buy` is
        charged to the long book and a `sell` releases it.
        """
        # Atomic: acquire lock before reading state
        try:
            fd = _acquire_lock()
        except TimeoutError:
            return RiskVerdict(False, "could not acquire risk lock (concurrent access)", {})
        try:
            # Reload state from disk (another process may have updated it)
            self.state = self._load_state()
            checks = {}
            notional = Decimal(str(notional))
            is_short = self._is_short(direction, short)

            # 1. Kill switch
            if self.check_kill_switch():
                checks["kill_switch"] = "ENGAGED"
                return RiskVerdict(False, "global kill switch engaged", checks)

            # 2. Duplicate order
            if self.check_duplicate(symbol, side, strategy):
                checks["duplicate"] = "BLOCKED"
                return RiskVerdict(False, f"duplicate {side} {symbol} within {self._duplicate_cooldown_sec}s cooldown", checks)

            # 3. Exposure limits
            ok, reason = self.check_exposure(symbol, notional, short=is_short)
            checks["exposure"] = reason
            if not ok:
                return RiskVerdict(False, reason, checks)

            # 4. Drawdown
            if current_equity > 0:
                ok, reason = self.check_drawdown(current_equity)
                checks["drawdown"] = reason
                if not ok:
                    return RiskVerdict(False, reason, checks)

            # 5. Stale data
            if data_age_sec > 0:
                ok, reason = self.check_stale_data(data_age_sec)
                checks["stale_data"] = reason
                if not ok:
                    return RiskVerdict(False, reason, checks)

            checks["all"] = "PASS"
            return RiskVerdict(True, "approved", checks)
        finally:
            _release_lock(fd)

    def note_order(self, symbol: str, side: str, strategy: str,
                   notional: Decimal | float = 0) -> None:
        """Record an *attempt* for the duplicate cooldown, charging no exposure.

        The day-trade engine calls this when it forwards a plan to the
        controller and charges the book only once the order reaches the broker
        (`record_order`). Splitting the two keeps the cooldown working in
        dry-run — where nothing is ever charged — without a dry-run order
        consuming notional budget for a position it never opened.
        """
        try:
            fd = _acquire_lock()
        except TimeoutError:
            return
        try:
            self.state = self._load_state()
            self._note_recent_order(symbol, side, strategy, notional)
            self._save_state()
        finally:
            _release_lock(fd)

    def _note_recent_order(self, symbol: str, side: str, strategy: str,
                           notional: Decimal | float) -> None:
        """Append to `recent_orders` (the cooldown's only source) and prune."""
        self.state.recent_orders.append({
            "symbol": symbol, "side": side, "strategy": strategy,
            "ts": datetime.now(ET).isoformat(), "notional": str(notional),
        })
        # Prune old orders (keep last 24h)
        cutoff = datetime.now(ET) - timedelta(hours=24)
        self.state.recent_orders = [
            o for o in self.state.recent_orders
            if datetime.fromisoformat(o["ts"]) > cutoff
        ]

    def record_order(self, symbol: str, side: str, strategy: str,
                     notional: Decimal | float = 0,
                     direction: str | None = None,
                     *, short: bool | None = None) -> None:
        """Record a placed order for duplicate detection and exposure tracking.

        `direction`/`short` say which book this order touches. With no
        direction, `buy` opens a long and `sell` releases it (today's
        behaviour, unchanged). With `short=True` the meaning inverts: a `sell`
        opens the short book and a `buy` releases it.

        A release removes only its own book's row, so closing a long cannot
        un-book a short in the same symbol (or the reverse).
        """
        try:
            fd = _acquire_lock()
        except TimeoutError:
            return
        try:
            self.state = self._load_state()
            self._note_recent_order(symbol, side, strategy, notional)
            # Track exposure. `open_positions` stays the source of truth; the
            # direction stored on the row is what _regroup() groups by. A side
            # that is neither buy nor sell still does nothing, as before.
            is_short = self._is_short(direction, short)
            if side in ("buy", "sell"):
                opens = (side == "sell") if is_short else (side == "buy")
                if opens:
                    self.state.open_positions[self._position_key(symbol, is_short)] = {
                        "strategy": strategy, "notional": str(notional),
                        "entered_at": datetime.now(ET).isoformat(),
                        "direction": "short" if is_short else "long",
                    }
                else:
                    self.state.open_positions.pop(
                        self._position_key(symbol, is_short), None)
                    if not is_short:
                        # A ledger written before the books were split keys
                        # this long row by the bare symbol.
                        self.state.open_positions.pop(symbol, None)
            self.state._regroup()
            self._save_state()
        finally:
            _release_lock(fd)

    def remove_position(self, symbol: str, short: bool | None = None) -> None:
        """Remove a position from tracking (after exit).

        With `short` given, only that book's row goes, so a long and a short
        in one symbol stay independent. Without it every row for the symbol is
        removed, including the bare-symbol row a pre-split ledger may hold.
        """
        books = (("short",) if short else ("long",)) if short is not None \
            else ("long", "short")
        for book in books:
            self.state.open_positions.pop(f"{book}:{symbol}", None)
        if short is None or short is False:
            self.state.open_positions.pop(symbol, None)
        self.state._regroup()
        self._save_state()

    def status(self) -> dict:
        """Current controller status."""
        long_notional = self._book_notional("long")
        short_notional = self._book_notional("short")
        return {
            "kill_switch": self.check_kill_switch(),
            "open_positions": self.state.open_positions,
            "recent_orders_24h": len(self.state.recent_orders),
            "peak_equity": self.state.peak_equity,
            "last_updated": self.state.last_updated,
            "exposure": {
                "long_notional": float(long_notional),
                "short_notional": float(short_notional),
                "max_per_book": float(self._max_book_notional),
                "total_notional": float(long_notional + short_notional),
            },
        }


def main(argv=None) -> int:
    """CLI gate: exit 0 = approved, exit 1 = rejected."""
    import argparse
    p = argparse.ArgumentParser(description="Portfolio risk gate.")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--notional", type=float, default=0)
    p.add_argument("--strategy", default="unknown")
    p.add_argument("--data-age", type=float, default=0)
    p.add_argument("--equity", type=float, default=0)
    p.add_argument("--direction", choices=["long", "short"], default="long",
                   help="book this order touches (default: long)")
    p.add_argument("--short", action="store_true",
                   help="shorthand for --direction short: a sell opens the short book")
    p.add_argument("--engage-kill", action="store_true")
    p.add_argument("--clear-kill", action="store_true")
    args = p.parse_args(argv)

    ctrl = PortfolioRiskController()

    if args.engage_kill:
        ctrl.engage_kill_switch("CLI")
        print("kill switch engaged")
        return 0
    if args.clear_kill:
        ctrl.clear_kill_switch()
        print("kill switch cleared")
        return 0

    # Without --short/--direction short a sell is recorded as a release, so the
    # short book would silently read zero while the position is live.
    short = True if args.short else None
    verdict = ctrl.check(args.symbol, args.side, Decimal(str(args.notional)),
                         args.strategy, args.data_age, args.equity,
                         direction=args.direction, short=short)
    print(json.dumps({"approved": verdict.approved, "reason": verdict.reason,
                      "checks": verdict.checks}))
    if verdict.approved:
        ctrl.record_order(args.symbol, args.side, args.strategy, args.notional,
                          direction=args.direction, short=short)
    return 0 if verdict.approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
