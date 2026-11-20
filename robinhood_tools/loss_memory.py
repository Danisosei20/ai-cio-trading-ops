"""Per-symbol loss memory: a loser gets a rest before it gets re-bought.

Observed twice in one week: RDDT lost 40% on 2026-11-10 (realized -825 on
one contract, -1150 on the next) and the 15:10 watch bought RDDT puts
again the same session; TTWO lost 24% on 2026-11-16. The desk had a
time-based symbol cooldown but nothing keyed to *losing*.

Computable only since fill reconciliation (2026-11-16): order_fills now
holds the broker's complete fill history, so round-trips per underlier
can be paired and their realized P/L summed without trusting the
one-row-per-symbol lifecycle table.

Pure over its inputs: `underlier`, `round_trips` and `loss_streak_verdict`
take the fill rows and a date, so the policy is testable without a
database. The database method below is the only stateful part.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

OCC = re.compile(r"^([A-Z]{1,6})\d{6}[CP]\d{8}$")

# Policy: two consecutive losing round-trips on one underlier rest it for
# five trading days. One loss is a loss; two in a row is a pattern.
MAX_LOSS_STREAK = 2
LOSS_COOLDOWN_DAYS = 5
# Only recent history governs: a bad month is not a bad week.
WINDOW_DAYS = 30


def underlier(symbol: str) -> str:
    """RDDT270115P00095000 -> RDDT; NVDA -> NVDA; BRK-B -> BRK-B."""
    text = str(symbol or "").strip().upper()
    match = OCC.match(text)
    return match.group(1) if match else text


def contract_multiplier(symbol: str) -> Decimal:
    """Contracts are quoted per share; an option position is 100x that."""
    return Decimal("100") if OCC.match(str(symbol or "").strip().upper()) else Decimal("1")


def _as_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        try:
            return date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class RoundTrip:
    underlier: str
    quantity: Decimal
    pnl: Decimal
    closed_on: date


def round_trips(fills: list[dict]) -> list[RoundTrip]:
    """FIFO-pair the desk's own fills into realized round-trips.

    The desk is long-only, so a sell closes buys against average cost.
    Unpaired buys (the open position) are not round-trips. P/L is
    (sell_avg - buy_avg) x closed_qty, contracts at their multiplier.
    """
    by_symbol: dict[str, list[dict]] = {}
    for row in fills:
        symbol = str(row.get("symbol", "")).strip().upper()
        if symbol:
            by_symbol.setdefault(symbol, []).append(row)

    trips: list[RoundTrip] = []
    for symbol, rows in by_symbol.items():
        rows = sorted(rows, key=lambda r: str(r.get("filled_at", "")))
        mult = contract_multiplier(symbol)
        buys: list[tuple[Decimal, Decimal]] = []  # (qty, price)
        buy_day: date | None = None
        for row in rows:
            side = str(row.get("side", "")).lower()
            try:
                qty = Decimal(str(row.get("quantity", "0")))
                price = Decimal(str(row.get("price", "0")))
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            if side == "buy":
                buys.append((qty, price))
                buy_day = buy_day or _as_date(row.get("filled_at", ""))
                continue
            if side != "sell":
                continue
            remaining = qty
            sell_value = price * qty
            cost = Decimal("0")
            closed_qty = Decimal("0")
            while remaining > 0 and buys:
                buy_qty, buy_price = buys[0]
                take = min(buy_qty, remaining)
                cost += take * buy_price
                closed_qty += take
                remaining -= take
                if take >= buy_qty:
                    buys.pop(0)
                else:
                    buys[0] = (buy_qty - take, buy_price)
            if closed_qty <= 0:
                continue
            closed_on = _as_date(row.get("filled_at", "")) or buy_day or date.today()
            trips.append(RoundTrip(
                underlier=underlier(symbol), quantity=closed_qty,
                pnl=((sell_value - cost) * mult).quantize(Decimal("0.01")),
                closed_on=closed_on))
    trips.sort(key=lambda t: t.closed_on)
    return trips


@dataclass(frozen=True)
class LossVerdict:
    blocked: bool
    streak: int
    last_loss_on: date | None
    blocked_through: date | None
    reason: str


def loss_streak_verdict(trips: list[RoundTrip], *, symbol: str, today: date,
                        max_losses: int = MAX_LOSS_STREAK,
                        window_days: int = WINDOW_DAYS,
                        cooldown_days: int = LOSS_COOLDOWN_DAYS) -> LossVerdict:
    """Consecutive most-recent losses on this underlier, inside the window.

    A win resets the streak; a loss older than the window is forgotten.
    Two losses in a row block new entries through the last loss plus the
    cooldown. Never blocked on missing history: an empty list is allowed
    (the caller still fails closed at the exchange gates that need data).
    """
    target = underlier(symbol)
    mine = [t for t in trips if t.underlier == target]
    if not mine:
        return LossVerdict(False, 0, None, None, "no history")

    streak = 0
    last_loss: date | None = None
    for trip in reversed(mine):  # newest first
        if (today - trip.closed_on).days > window_days:
            break
        if trip.pnl < 0:
            streak += 1
            last_loss = trip.closed_on
        else:
            break

    if streak >= max_losses and last_loss is not None:
        through = last_loss.fromordinal(last_loss.toordinal() + cooldown_days)
        if today <= through:
            return LossVerdict(
                True, streak, last_loss, through,
                f"{streak} consecutive losses on {target} "
                f"(last {last_loss.isoformat()}): memory through "
                f"{through.isoformat()}")
    return LossVerdict(False, streak, last_loss, None,
                       f"streak {streak} on {target}")