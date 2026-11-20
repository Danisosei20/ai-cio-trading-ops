#!/usr/bin/env python3
"""Options position guard: time, profit, and loss exits for long contracts.

Evaluates every open OCC position each run:
  EXPIRE  DTE <= --max-dte (default 2) — never hold into expiry weekend
  STOP    premium down <= --stop-pct (default 0.15 = -15%)
  TARGET  premium up >= --target-pct (default 0 = DISABLED — winners run)
  STALE   held >= --max-hold-days (default 5) while NOT in profit
  TRAIL   if peak >= 25%: floor = peak * (1 - stop_pct/trail_arm)
          (trail_arm=0.25, so floor = peak * 0.40). Exit if current <= floor.
Exits sell-to-close through review + ledger + session guard. Paper only.

ACTUAL POLICY (reconciled 2026-11-16):
  - Take-profit: DISABLED by default — winners run unlimited. The
    2026-11-06 +2% target ("take trade at any time even if the profit is
    2%") was reverted by the owner on 2026-11-16 ("no, it should be
    unlimited"), after the options Time Machine showed a 1:7.5 payoff at
    a 61% win rate is negative EV per round trip (-$47.53/contract on
    the live RDDT position). Protection comes from the trail instead:
    above +25% the ratcheting floor (peak x 0.40) locks the winner in,
    so a +200% winner can never ride back below +80%. --target-pct still
    accepts an explicit value for anyone who wants a scalp; 0 disables.
  - Stop loss: -15% from entry (not -25%; the -25% in prior docs was the
    equity guard's stop, not the options guard's). Gap caveat: a weekend
    gap can fill far past it — hence the Thursday 15:45 short-premium rule.
  - Trailing: activates at +25% peak. Floor = peak * 0.40 (i.e., a +100%
    winner locks in +40%). Once armed, the stop ratchets from the peak.
  - Gap risk: a -15% (or -25%) stop is NOT a guaranteed max loss. Options
    can gap past the stop during illiquid or overnight sessions. Market
    orders on illiquid contracts may fill far from the intended price.
    The pilot's small notional limits exposure to this risk.

The EQUITY guard (paper_position_guard.py) is a separate policy: it keeps its
+2%/-1% thresholds. This options guard does NOT share them — its take-profit
is disabled by default and protection comes from the -15% stop and +25% trail.

  python3 scripts/options_guard.py --check
  python3 scripts/options_guard.py --execute
State: outputs/paper/options_guard.json (first-seen dates).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.errors import PolicyViolation  # noqa: E402

ET = ZoneInfo("America/New_York")
from robinhood_tools.options import occ_details  # noqa: E402
from robinhood_tools.options_flow import close_long_option  # noqa: E402
from robinhood_tools.runtime import build_settings  # noqa: E402

STATE_FILE = Path("outputs/paper/options_guard.json")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Exit guard for long option positions.")
    p.add_argument("--check", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--target-pct", type=Decimal, default=Decimal("0"),
                   help="take-profit threshold (default 0 = DISABLED, winners "
                        "run unlimited; the +25% trail floor protects them). "
                        "The 2026-11-06 +2% default was reverted by the owner "
                        "on 2026-11-16 after the options Time Machine showed a "
                        "1:7.5 payoff at a 61% win rate is negative EV per "
                        "round trip (-$47.53/contract on the live position)")
    p.add_argument("--stop-pct", type=Decimal, default=Decimal("0.15"))
    p.add_argument("--max-dte", type=int, default=2)
    p.add_argument("--max-hold-days", type=int, default=5)
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def decide(entry_cost: Decimal, mark_value: Decimal, dte: int, hold_days: int,
           target_pct: Decimal, stop_pct: Decimal,
           max_dte: int, max_hold_days: int,
           peak_ret: Decimal | None = None) -> tuple[str, str]:
    """Pure exit decision. Returns (action, reason).

    Active policy (the owner reverted the +2% target on 2026-11-16): an
    ordinary winner is HELD — ``target_pct`` defaults to 0, so TARGET fires
    only when a caller explicitly passes a positive threshold. Protection
    comes from the -15% stop and the trail: once a position has peaked above
    +25%, the stop ratchets up to a floor of peak * 0.40, so a +200% winner
    can never ride back below +80% and is never sold at a flat target.
    """
    if entry_cost <= 0:
        raise PolicyViolation("Entry cost must be positive.")
    ret = mark_value / entry_cost - 1
    if dte <= max_dte:
        return "EXPIRE", f"DTE {dte} <= {max_dte}"
    trail_arm = Decimal("0.25")
    peak = peak_ret if peak_ret is not None else ret
    if peak >= trail_arm:
        floor = peak * (1 - stop_pct / trail_arm)
        if floor > 0:
            if ret <= floor:
                return "TRAIL", f"peak +{peak:.0%} -> floor +{floor:.0%}, now {ret:.0%}"
            # Past the trail arm, the ratcheting floor is the protection and
            # the position keeps riding. Without this boundary the +2%
            # take-profit would close a home run the moment it crossed +2% -
            # the ACN call sits above +100% and a flat target would sell it.
            return ("HOLD",
                    f"peak +{peak:.0%} -> trail armed, floor +{floor:.0%}, "
                    f"now +{ret:.0%}")
        # A degenerate config (stop_pct >= trail_arm) leaves no valid floor;
        # fall through to the ordinary rules rather than inventing one.
    if ret <= -stop_pct:
        return "STOP", f"premium {ret:.0%} <= -{stop_pct:.0%}"
    if target_pct > 0 and ret >= target_pct:
        return "TARGET", f"premium +{ret:.0%} >= +{target_pct:.0%}"
    if ret > 0:
        return "HOLD", f"profit {ret:.0%} — winners run (trail armed at +{trail_arm:.0%})"
    if hold_days >= max_hold_days:
        return "STALE", f"held {hold_days}d at {ret:.0%}"
    return "HOLD", f"premium {ret:.0%}, DTE {dte}, day {hold_days}"


def _load_tracked(path: Path) -> dict:
    """Symbol -> {first_seen, cost}, tolerating the legacy nested schema.

    Older builds wrote the whole previous state document under "tracked", so
    symbol records can sit at several depths. Walk the tree and keep the
    earliest first_seen seen for each symbol so hold-days stays monotonic.
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (ValueError, OSError):
        return {}

    merged: dict = {}

    def walk(node) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key in ("updated", "evaluated"):
                continue
            if key == "tracked":
                walk(value)
                continue
            if isinstance(value, dict) and "first_seen" in value:
                prior = merged.get(key)
                if prior is None or str(value.get("first_seen")) < str(
                        prior.get("first_seen")):
                    merged[key] = {"first_seen": value.get("first_seen"),
                                   "cost": value.get("cost")}

    walk(raw)
    return merged


def main(argv=None) -> int:
    args = parse_args(argv)
    settings = build_settings(args.config, args.env_file)
    if settings.trading_enabled:
        print("REFUSAL: live kill switch open.", file=sys.stderr)
        return 2

    import os

    from robinhood_tools.alpaca_options import AlpacaOptionsData
    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
    from robinhood_tools.settings import load_env

    env = {**load_env(args.env_file), **os.environ}
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    data = AlpacaOptionsData.from_values(env)
    state = _load_tracked(STATE_FILE)
    today = date.today()

    results = []
    for p in backend.list_positions():
        symbol = str(p.get("symbol", ""))
        if len(symbol) <= 10:
            continue  # not an OCC contract
        rec = {"symbol": symbol}
        try:
            contract = occ_details(symbol)
            qty = int(float(str(p.get("qty", "0"))))
            if qty <= 0:
                continue
            cost = abs(Decimal(str(p.get("cost_basis", "0"))))
            dte = (date.fromisoformat(contract.expiry) - today).days
            seen = state.get(symbol, {})
            if "first_seen" not in seen:
                seen = {"first_seen": today.isoformat(), "cost": str(cost)}
                state[symbol] = seen
            hold_days = (today - date.fromisoformat(seen["first_seen"])).days
            snaps = data.snapshots([symbol])
            snap = snaps.get(symbol) or {}
            bid = Decimal(str((snap.get("latestQuote") or {}).get("bp", "0")))
            mark_value = bid * qty * 100
            ret = (mark_value / cost - 1) if cost > 0 else Decimal("-1")
            # Ratchet: remember the best return this position ever reached.
            try:
                prior_peak = Decimal(seen.get("peak", "0"))
            except InvalidOperation:
                prior_peak = Decimal("0")
            peak = max(prior_peak, ret)
            seen["peak"] = str(peak)
            action, reason = decide(cost, mark_value, dte, hold_days,
                                   args.target_pct, args.stop_pct,
                                   args.max_dte, args.max_hold_days,
                                   peak_ret=peak)
            rec.update({"qty": qty, "dte": dte, "hold_days": hold_days,
                        "return": f"{ret:.0%}" if cost > 0 else None,
                        "peak": f"+{peak:.0%}",
                        "signal": action, "reason": reason})
            if action != "HOLD" and args.execute:
                from robinhood_tools.alpaca_options import snapshot_to_quote  # noqa: E402
                from robinhood_tools.options import occ_details as _occ  # noqa: E402
                from robinhood_tools.alpaca_market_data import (  # noqa: E402
                    AlpacaMarketDataHttpClient)
                from robinhood_tools.market_prices import get_quote as _spot

                contract = _occ(symbol)
                snaps = data.snapshots([symbol])
                snap = snaps.get(symbol) or {}
                lq = snap.get("latestQuote") or {}
                if not lq.get("bp"):
                    raise PolicyViolation(f"No bid for {symbol}; cannot exit into air.")
                under = _spot(contract.underlying,
                              data_client=AlpacaMarketDataHttpClient.from_values(env)).price
                quote = snapshot_to_quote(contract, snap, under)
                result = close_long_option(
                    settings=settings, quote=quote, contracts=qty, database=None,
                    service=None, today=today)
                if result["status"] in {"queued", "confirmed", "filled",
                                        "partially_filled", "accepted"}:
                    rec.update({"exit_action": "sell_placed",
                                "order_id": result["order_id"], "status": result["status"],
                                # The Slack alert's outcome, carried through so a
                                # failed alert on an exit is visible here rather
                                # than only inferable after the fact.
                                "alert": result.get("alert", "unknown")})
                    state.pop(symbol, None)
                else:
                    rec.update({"exit_action": "not_accepted",
                                "status": result["status"],
                                "alert": result.get("alert", "unknown")})
            results.append(rec)
            print(json.dumps(rec))
        except Exception as exc:  # noqa: BLE001 - a broker error must never kill the guard
            # (e.g. HTTP 403 when options trading is not enabled on the account:
            # the exit fails loudly here and in the state file, the run continues)
            results.append({"symbol": symbol, "action": "error",
                            "reason": f"{type(exc).__name__}: {exc}"})
            print(json.dumps(results[-1]))
    # Drop symbols no longer held so a later re-entry starts a fresh first_seen
    # (otherwise it would inherit an old date and trip STALE prematurely).
    held = {str(r.get("symbol")) for r in results if r.get("symbol")}
    state = {sym: rec for sym, rec in state.items() if sym in held}
    if not results:
        # Every run leaves a trace. A silent exit 0 is indistinguishable
        # from "the guard did nothing" when there is nothing to do - and on
        # 2026-11-17 that ambiguity made a correctly-working stop-loss look
        # like an unfired one (the exit limit was working for 69 minutes).
        print(json.dumps({"action": "no_open_option_positions",
                          "checked_at": datetime.now(ET).isoformat()}))

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"updated": today.isoformat(), "tracked": state,
                                      "evaluated": results}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
