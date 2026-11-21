#!/usr/bin/env python3
"""Trade state machine for autonomous long/short day trading.

Persistent state machine tracking the lifecycle of each trade:
  SCANNING → SIGNAL_FOUND → RISK_PENDING → ORDER_PENDING → PARTIALLY_FILLED
  → POSITION_OPEN → EXIT_PENDING → CLOSED

Also supports: REJECTED, CANCELED, EXPIRED, RECONCILIATION_REQUIRED.

Recover safely after restarts using actual broker positions and orders.
No duplicate orders after retries or restarts.

Usage:
  from robinhood_tools.trade_state import TradeStateMachine, TradeState
  sm = TradeStateMachine()
  trade = sm.create_trade(ticker="NVDA", direction="long", setup="orb_breakout")
  sm.transition(trade.id, TradeState.SIGNAL_FOUND)
  sm.transition(trade.id, TradeState.RISK_PENDING)
  ...
  # On restart:
  sm.recover_from_broker(broker_positions, broker_orders)
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
STATE_FILE = ROOT / "outputs" / "paper" / "trade_state_machine.json"


class TradeState(str, Enum):
    SCANNING = "SCANNING"
    SIGNAL_FOUND = "SIGNAL_FOUND"
    RISK_PENDING = "RISK_PENDING"
    ORDER_PENDING = "ORDER_PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    POSITION_OPEN = "POSITION_OPEN"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


# Valid state transitions
VALID_TRANSITIONS = {
    TradeState.SCANNING: {TradeState.SIGNAL_FOUND, TradeState.REJECTED},
    TradeState.SIGNAL_FOUND: {TradeState.RISK_PENDING, TradeState.REJECTED, TradeState.CANCELED},
    TradeState.RISK_PENDING: {TradeState.ORDER_PENDING, TradeState.REJECTED},
    TradeState.ORDER_PENDING: {TradeState.PARTIALLY_FILLED, TradeState.POSITION_OPEN,
                               TradeState.CANCELED, TradeState.EXPIRED,
                               TradeState.RECONCILIATION_REQUIRED},
    TradeState.PARTIALLY_FILLED: {TradeState.POSITION_OPEN, TradeState.EXIT_PENDING,
                                  TradeState.CANCELED, TradeState.RECONCILIATION_REQUIRED},
    TradeState.POSITION_OPEN: {TradeState.EXIT_PENDING, TradeState.CLOSED,
                               TradeState.RECONCILIATION_REQUIRED},
    TradeState.EXIT_PENDING: {TradeState.CLOSED, TradeState.RECONCILIATION_REQUIRED},
    TradeState.CLOSED: set(),
    TradeState.REJECTED: set(),
    TradeState.CANCELED: set(),
    TradeState.EXPIRED: set(),
    TradeState.RECONCILIATION_REQUIRED: {TradeState.POSITION_OPEN, TradeState.CLOSED,
                                         TradeState.REJECTED},
}


@dataclass
class TradeRecord:
    id: str
    ticker: str
    direction: str  # "long" or "short"
    setup: str
    state: str = TradeState.SCANNING.value
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    risk_pct: float = 0.0
    qty: float = 0.0
    filled_qty: float = 0.0
    entry_order_id: str = ""
    exit_order_id: str = ""
    strategy: str = ""
    regime: str = ""
    odds: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    closed_at: str = ""
    close_reason: str = ""
    realized_pnl: float = 0.0
    client_order_id: str = ""  # idempotent key


class TradeStateMachine:
    """Persistent trade state machine with restart recovery."""

    def __init__(self, state_path=None):
        self.state_path = state_path or STATE_FILE
        self.trades: dict[str, TradeRecord] = {}
        self._load()

    def _load(self) -> None:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                for tid, data in raw.get("trades", {}).items():
                    self.trades[tid] = TradeRecord(**{k: v for k, v in data.items()
                                                      if k in TradeRecord.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError, OSError):
                pass

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"trades": {tid: asdict(t) for tid, t in self.trades.items()},
                "updated": datetime.now(ET).isoformat()}
        self.state_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def create_trade(self, ticker: str, direction: str, setup: str,
                     strategy: str = "", regime: str = "",
                     entry_price: float = 0, stop_price: float = 0,
                     target_price: float = 0, risk_pct: float = 0,
                     qty: float = 0, client_order_id: str = "") -> TradeRecord:
        """Create a new trade in SCANNING state."""
        import uuid
        tid = f"{ticker}:{direction}:{datetime.now(ET).date().isoformat()}:{uuid.uuid4().hex[:8]}"
        if not client_order_id:
            client_order_id = tid
        trade = TradeRecord(
            id=tid, ticker=ticker, direction=direction, setup=setup,
            strategy=strategy, regime=regime, entry_price=entry_price,
            stop_price=stop_price, target_price=target_price, risk_pct=risk_pct,
            qty=qty, client_order_id=client_order_id,
            created_at=datetime.now(ET).isoformat(),
            updated_at=datetime.now(ET).isoformat(),
        )
        self.trades[tid] = trade
        self._save()
        return trade

    def transition(self, trade_id: str, new_state: TradeState,
                   reason: str = "") -> bool:
        """Transition a trade to a new state. Returns True if valid."""
        trade = self.trades.get(trade_id)
        if trade is None:
            return False
        current = TradeState(trade.state)
        if new_state not in VALID_TRANSITIONS.get(current, set()):
            return False
        trade.state = new_state.value
        trade.updated_at = datetime.now(ET).isoformat()
        if new_state == TradeState.CLOSED:
            trade.closed_at = datetime.now(ET).isoformat()
            trade.close_reason = reason
        self._save()
        return True

    def get_open_trades(self) -> list[TradeRecord]:
        """Get all trades in non-terminal states."""
        terminal = {TradeState.CLOSED.value, TradeState.REJECTED.value,
                    TradeState.CANCELED.value, TradeState.EXPIRED.value}
        return [t for t in self.trades.values() if t.state not in terminal]

    def get_trades_by_state(self, state: TradeState) -> list[TradeRecord]:
        return [t for t in self.trades.values() if t.state == state.value]

    def recover_from_broker(self, broker_positions: list[dict],
                            broker_orders: list[dict]) -> dict:
        """Reconcile state machine with actual broker state after restart.

        Returns a dict of reconciliation actions taken.
        """
        actions = {"matched": 0, "orphaned_positions": [], "missing_positions": [],
                   "reconciliation_required": []}
        broker_symbols = {p.get("symbol") for p in broker_positions}
        open_trades = self.get_open_trades()

        # Match open trades to broker positions
        for trade in open_trades:
            if trade.ticker in broker_symbols:
                # Update filled qty from broker
                for bp in broker_positions:
                    if bp.get("symbol") == trade.ticker:
                        trade.filled_qty = float(bp.get("qty", 0))
                        if trade.filled_qty > 0 and trade.state in (
                                TradeState.ORDER_PENDING.value,
                                TradeState.PARTIALLY_FILLED.value):
                            self.transition(trade.id, TradeState.POSITION_OPEN,
                                            "recovered from broker")
                        actions["matched"] += 1
                        break
            else:
                # Trade says open but broker doesn't have it
                if trade.state in (TradeState.ORDER_PENDING.value,
                                   TradeState.PARTIALLY_FILLED.value,
                                   TradeState.POSITION_OPEN.value):
                    self.transition(trade.id, TradeState.RECONCILIATION_REQUIRED,
                                    "position not found at broker")
                    actions["missing_positions"].append(trade.ticker)

        # Check for broker positions not tracked by state machine
        tracked_symbols = {t.ticker for t in open_trades}
        for bp in broker_positions:
            sym = bp.get("symbol")
            if sym and sym not in tracked_symbols:
                actions["orphaned_positions"].append(sym)

        self._save()
        return actions

    def mark_closed(self, trade_id: str, reason: str, realized_pnl: float = 0.0) -> bool:
        """Mark a trade as closed with P/L."""
        trade = self.trades.get(trade_id)
        if trade is None:
            return False
        if trade.state not in (TradeState.POSITION_OPEN.value, TradeState.EXIT_PENDING.value,
                               TradeState.RECONCILIATION_REQUIRED.value):
            return False
        trade.realized_pnl = realized_pnl
        self.transition(trade_id, TradeState.CLOSED, reason)
        return True

    def check_idempotency(self, client_order_id: str) -> bool:
        """Check if an order with this client_order_id already exists."""
        for trade in self.trades.values():
            if trade.client_order_id == client_order_id:
                return True
        return False

    def cleanup_old(self, days: int = 7) -> int:
        """Remove closed trades older than N days. Returns count removed."""
        cutoff = datetime.now(ET) - timedelta(days=days)
        to_remove = []
        for tid, trade in self.trades.items():
            if trade.state in (TradeState.CLOSED.value, TradeState.REJECTED.value,
                               TradeState.CANCELED.value, TradeState.EXPIRED.value):
                try:
                    if datetime.fromisoformat(trade.closed_at or trade.updated_at) <= cutoff:
                        to_remove.append(tid)
                except ValueError:
                    pass
        for tid in to_remove:
            del self.trades[tid]
        if to_remove:
            self._save()
        return len(to_remove)

    def summary(self) -> dict:
        """Summary of all trades."""
        states = {}
        for t in self.trades.values():
            states[t.state] = states.get(t.state, 0) + 1
        open_trades = self.get_open_trades()
        closed = [t for t in self.trades.values() if t.state == TradeState.CLOSED.value]
        total_pnl = sum(t.realized_pnl for t in closed)
        return {
            "total_trades": len(self.trades),
            "open": len(open_trades),
            "closed": len(closed),
            "states": states,
            "total_realized_pnl": round(total_pnl, 2),
        }


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Trade state machine.")
    p.add_argument("--status", action="store_true", help="show summary")
    p.add_argument("--recover", action="store_true", help="recover from broker")
    p.add_argument("--cleanup", type=int, default=0, help="remove closed trades older than N days")
    args = p.parse_args(argv)
    sm = TradeStateMachine()
    if args.status:
        import json as _j
        print(_j.dumps(sm.summary(), indent=2))
    elif args.cleanup:
        n = sm.cleanup_old(args.cleanup)
        print(f"removed {n} old trades")
    else:
        print(sm.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
