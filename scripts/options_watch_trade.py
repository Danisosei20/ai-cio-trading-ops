#!/usr/bin/env python3
"""15:10 options watch -> trade. Owner 2026-10-14:

    "when it send the options watch, the bot should be buying and selling.
     the option calls, if the call lost 25% it should exit."

Deterministic, zero-burn (no LLM). One cycle does three things, in order:

  1. EXITS   options_guard.py --execute    take +2% / cut -25% / DTE / stale
  2. ENTRY   best gated screener name      one entry per run (buy)
  3. NOTIFY  options_watchlist.py --send   the watch itself

`--loop N` repeats that whole cycle every N minutes until `--until` ET, so
the desk can trade across the session rather than once at 15:10. In loop
mode the notify only fires when a cycle actually placed something, because a
loop that Slack-messages every cycle is a loop nobody reads.

Entry gate (all deterministic, mirror of the trading-playbook):
  - candidate from the latest screener top-N, in rank order
  - RSI <= --max-rsi (default 73; playbook: <=~70 usable, avoid >75-77)
  - underlier not already held (no doubling a name)
  - direction follows 20d momentum (mom20 >= 0 -> call, else put)
  - regime: SPY below its 50-day mean (RISK_OFF) with --direction auto adds
    the screener's weak-momentum tail (weak.json) as puts to the ranked top,
    because a one-day slide cannot flip a 20-day lookback and the ranked
    top-N only ever surfaces strength. Both feeds go through the same gate
    (held + RSI + top), so the bear tape is offered both directions rather
    than one. Every other regime -- including UNKNOWN, which fails closed --
    and an explicit --direction call/put keep the ranked feed unchanged.
  - S&P 500 constituent or ETF allowlist, premium <= cap, <=5 options,
    <=5 contracts, earnings blackout, session window -- all enforced
    downstream by options_pilot -> place_long_option. We never bypass.
  - Liquid signal gate: skip CALL on crowded longs where smart money is
    fading; skip PUT on crowded shorts where smart money is buying.
    Read-only -- never places trades itself.

  python3 scripts/options_watch_trade.py                        # dry-run (default)
  python3 scripts/options_watch_trade.py --execute
  python3 scripts/options_watch_trade.py --execute --no-exits   # entry only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
# Run as `python scripts/options_watch_trade.py` puts scripts/ (not the repo
# root) on sys.path; robinhood_tools lives at the root. The fix has to run
# before the robinhood_tools import below, not after it.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robinhood_tools.market_prices import _spy_closes, market_regime  # noqa: E402
from robinhood_tools.desk_policy import POLICY  # noqa: E402

# TradingAgents venv interpreter. Prefer an explicit override, then portable
# sibling venv locations; never silently claim a missing machine path exists.
_TA_ROOT = ROOT.parent / "TradingAgents"
_TA_DEFAULTS = (_TA_ROOT / ".venv" / "Scripts" / "python.exe",
                _TA_ROOT / ".venv" / "bin" / "python")
TAVENV = Path(os.environ["TRADINGAGENTS_VENV_PYTHON"]) if os.environ.get(
    "TRADINGAGENTS_VENV_PYTHON") else next((p for p in _TA_DEFAULTS if p.exists()), Path(sys.executable))

ET = ZoneInfo("America/New_York")


def desk_python() -> str:
    """Interpreter that has the desk's deps (yfinance, alpaca, ...).

    The trading-playbook notes the TradingAgents venv is the safe choice
    for background jobs; repo .venv may be missing deps.
    """
    return str(TAVENV) if TAVENV.exists() else sys.executable


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="15:10 options watch -> trade.")
    p.add_argument("--screen", default="outputs/screener/latest.json")
    p.add_argument("--weak-screen", default="outputs/screener/weak.json",
                   help="bear-book feed (weakest mom20 first), used only "
                        "when the regime is RISK_OFF")
    p.add_argument("--screen-top", type=int, default=5)
    p.add_argument("--max-rsi", type=float, default=73.0,
                   help="skip names above this RSI (overbought gas)")
    p.add_argument("--direction", default="auto", choices=["auto", "call", "put"])
    p.add_argument("--premium-cap", type=Decimal, default=Decimal("500"))
    p.add_argument("--max-contracts", type=int, default=1,
                   help="hard paper-desk limit: one contract")
    p.add_argument("--max-positions", type=int, default=5)
    p.add_argument("--max-tries", type=int, default=10,
                   help="candidates to attempt before giving up this run "
                        "(default 10: with the RISK_OFF tape offering both "
                        "feeds, 4 starved the calls behind the puts)")
    p.add_argument("--risk-off-max-iv", type=float, default=0.45,
                   help="RISK_OFF quoted-IV cap for new long premium "
                        "(default 45%%; 0 disables this guard)")
    p.add_argument("--loop", type=int, default=0,
                   help="minutes between cycles; 0 = one run (default). A loop "
                        "runs the full cycle - exits, one entry, notify on "
                        "change - until --until ET or the session closes")
    p.add_argument("--until", default="15:45",
                   help="stop looping at this ET time (default 15:45, the "
                        "desk's latest entry time)")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--no-exits", action="store_true")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--liquid-signals", default="outputs/liquid/latest.json",
                   help="Liquid signals file from Co-Invest MCP (signal gate only)")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def risk_off_iv_block(*, regime: str, quoted_iv: float | None,
                      max_iv: float) -> str | None:
    """Refuse expensive new long-option premium in a risk-off tape."""
    if regime != "RISK_OFF" or max_iv <= 0 or quoted_iv is None:
        return None
    if quoted_iv > max_iv:
        return f"risk-off IV {quoted_iv:.1%} exceeds {max_iv:.1%} cap"
    return None


def risk_on_confirmation(report: dict, side: str) -> str | None:
    """Require candidate price/VWAP/EMA alignment before a RISK_ON entry."""
    profile = report.get("profile") if isinstance(report, dict) else None
    price = report.get("price") if isinstance(report, dict) else None
    if not isinstance(profile, dict) or price is None:
        return "RISK_ON VWAP/EMA confirmation missing"
    try:
        price = float(price)
        vwap = float(profile["vwap"])
        fast = float(profile["ema_fast"])
        slow = float(profile["ema_slow"])
    except (KeyError, TypeError, ValueError):
        return "RISK_ON VWAP/EMA confirmation missing"
    aligned = (price > vwap and price > fast and price > slow
               if side == "call" else
               price < vwap and price < fast and price < slow)
    return None if aligned else "RISK_ON VWAP/EMA confirmation failed"


def gated_candidates(screen_path: str | Path, top: int, max_rsi: float,
                     held: set[str], direction: str = "auto", *,
                     rows: list[dict] | None = None,
                     force_side: str | None = None) -> list[tuple[str, str]]:
    """Pure: rank-ordered (symbol, direction) that clear the RSI/held gate.

    One gate for both books. `rows` supplies the feed directly (the
    weak-momentum tail under RISK_OFF) instead of reading `screen_path`, and
    `force_side` pins the side for every emitted pair -- but the held filter,
    the RSI cap, the symbol cleanup and the `top` truncation are the same
    code either way. A second filter for the bear book would silently drop
    "no doubling a name" and the RSI cap, which is exactly the defect this
    shape avoids.
    """
    if rows is None:
        try:
            doc = json.loads(Path(screen_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        rows = doc.get("ranked") or doc.get("rows") or []
    out: list[tuple[str, str]] = []
    for row in rows:
        if len(out) >= top:
            break
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        symbol = symbol.replace(".", "-")
        if not symbol or symbol in held:
            continue
        rsi = row.get("rsi")
        if rsi is not None and float(rsi) > max_rsi:
            continue
        if force_side is not None:
            side = force_side
        elif direction != "auto":
            side = direction
        else:
            side = _direction_for_row(row)
        out.append((symbol, side))
    return out


def _direction_for_row(row: dict, regime: str = "auto") -> str:
    """Per-row side rule: mom20 >= 0 -> call, else put.

    `regime` is accepted so a per-row regime rule has a seam, and is
    deliberately not read today: no regime flips this answer. RISK_OFF is
    applied upstream instead, by choosing the weak feed and pinning the side,
    so the per-row rule stays identical for whatever rows it is handed.
    """
    mom = row.get("mom20")
    return "call" if (mom is None or float(mom) >= 0) else "put"


def _regime() -> str:
    """RISK_ON / CHOP / RISK_OFF / UNKNOWN, failing closed to UNKNOWN.

    `market_regime` is pure; `_spy_closes` is the only network edge and
    already returns [] on any failure (which maps to UNKNOWN). The try/except
    keeps a surprise here from costing the run its entries.
    """
    try:
        return market_regime(_spy_closes(), require_long_term=True)
    except Exception:  # noqa: BLE001 - an unreadable regime is never RISK_ON
        return "UNKNOWN"


def weak_rows(path: str | Path) -> list[dict]:
    """Bear-book rows out of the screener's weak.json tail. [] on any error."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = doc.get("weak")
    except (OSError, ValueError, AttributeError):
        return []
    return rows if isinstance(rows, list) else []


def held_underliers(env_file: str) -> set[str] | None:
    """Underliers we already hold an OCC contract in.

    Fail-closed: returns None when the broker state cannot be read, so the
    caller must block entries instead of pretending we hold nothing. A silent
    except here once reported held=[] with a live position open, disabling
    the "no doubling a name" gate.
    """
    try:
        import os

        from robinhood_tools.alpaca_paper import (
            AlpacaPaperBackend, AlpacaPaperHttpTransport)
        from robinhood_tools.options import occ_details
        from robinhood_tools.settings import load_env

        env = {**load_env(env_file), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        held: set[str] = set()
        for pos in backend.list_positions():
            symbol = str(pos.get("symbol", ""))
            if len(symbol) > 10:  # OCC contract
                try:
                    held.add(occ_details(symbol).underlying)
                except Exception:
                    pass
        return held
    except Exception as exc:
        print(f"[entry] holdings check FAILED ({type(exc).__name__}: {exc}); "
              "entries blocked this run (fail-closed)", file=sys.stderr)
        return None


def liquid_signal_filter(symbol: str, side: str,
                         signals_path: str | Path) -> tuple[bool, str]:
    """Pure: apply Liquid signal gate to one candidate.

    Returns (allowed, reason).

    Rules (read-only signals, never execution):
    - Skip a CALL on a symbol that is a crowded long where smart money is
      fading — the crowd is already long, the smart money is exiting.
    - Skip a PUT on a symbol that is a crowded short where smart money is
      buying — the crowd is already short, the smart money is entering.
    - Everything else passes through.
    """
    try:
        doc = json.loads(Path(signals_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True, "no liquid signals file"
    signals = doc.get("signals", {}) or {}
    crowded_longs = set(signals.get("crowded_longs_smart_money_fading", []) or [])
    crowded_shorts = set(signals.get("crowded_shorts_smart_money_buying", []) or [])
    if side == "call" and symbol in crowded_longs:
        return False, "liquid: crowded long, smart money fading"
    if side == "put" and symbol in crowded_shorts:
        return False, "liquid: crowded short, smart money buying"
    return True, "liquid: signal clear"


def run(cmd: list[str]):
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def _tail(text: str, n: int = 160) -> str:
    return (text or "").strip().replace("\n", " ")[-n:]


def _ranked_rows(path: str | Path) -> list:
    """The long book's ranked rows, for the reason log and the bear feed's
    long half. Same shape `gated_candidates` reads when `rows` is not given."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = doc.get("ranked") or doc.get("rows") or []
    except (OSError, ValueError):
        return []
    return rows if isinstance(rows, list) else []


def _record_why(why: dict, rows: list, feed: str,
                forced_side: str | None = None) -> None:
    """Record *why* a candidate is on the list, so a purchase is auditable.

    A log line that says only "PLACED LITE put" is not a reason. This writes
    the screen row that produced the candidate: which feed, the momentum, RSI
    and price, and the side chosen - the same numbers the gates were given.

    First writer wins, so a name carried by both feeds keeps the reason for
    the side it was actually offered as.
    """
    for row in rows or []:
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        symbol = symbol.replace(".", "-")
        if not symbol:
            continue
        side = forced_side or _direction_for_row(row)
        try:
            mom = float(row.get("mom20") or 0.0)
            rsi = float(row.get("rsi") or 0.0)
            price = float(row.get("price") or 0.0)
        except (TypeError, ValueError):
            continue
        why.setdefault(symbol,
                       f"{feed} feed side={side} mom20={mom:+.1%} "
                       f"rsi={rsi:.0f} ${price:,.2f}")


def _balanced_end(text: str, start: int) -> int:
    """Index of the '}' matching the '{' at `start`, string-aware.

    Returns -1 when the object never closes. Braces inside string values
    (a contract symbol never has one, but a pandas warning dict does) do
    not move the depth.
    """
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _pilot_action(pilot_out: str) -> str:
    """The pilot's own verdict, for the log. Plain text, never a guess.

    Scans for the JSON object rather than requiring a line to start with
    `{`: yfinance prints deprecation warnings during the quote fetch, and
    the pilot prints its record with `json.dumps(rec, indent=2)`, so the
    JSON is rarely one clean line. 2026-11-16: parsing line-by-line
    reported "no-json" for 13 purchases the pilot had actually confirmed,
    which hid every window/premium refusal reason from the log too.
    """
    text = pilot_out or ""
    idx = text.find("{")
    while idx >= 0:
        end = _balanced_end(text, idx)
        if end > idx:
            try:
                obj = json.loads(text[idx:end + 1])
            except ValueError:
                obj = None
            if isinstance(obj, dict) and "action" in obj:
                return str(obj["action"])
        idx = text.find("{", idx + 1)
    return "no-json"


def _run_cycle(args, py: str) -> str | None:
    """One full watch cycle: exits, then one gated entry, then a notify.

    Returns the placement summary when something was bought (or would have
    been in dry-run), else None. The notify only fires on a change, because a
    loop that Slack-messages every cycle is a loop nobody reads.
    """
    # 1. EXITS -- take profit / stop out the book before considering entries.
    if not args.no_exits:
        guard_mode = "--execute" if args.execute else "--check"
        rc, out, err = run([py, "scripts/options_guard.py", guard_mode])
        print(f"[exits] rc={rc} {_tail(out) or _tail(err)}")

    # 2. ENTRY -- one gated screener name, placed through the gated flow.
    placed = None
    screen_date = None
    try:
        screen_date = json.loads(
            Path(args.screen).read_text(encoding="utf-8")).get("date")
    except (OSError, ValueError):
        pass
    held = held_underliers(args.env_file)
    pending = open_order_underliers(args.env_file)
    why: dict = {}
    if held is None or pending is None:
        # Fail-closed on either unreadable state: an empty set means "nothing
        # held / nothing working", which is a real answer; None means "could
        # not find out", which is not permission to trade.
        candidates = []
    else:
        # A name with an unfilled order is out of this run's reach: the order
        # is already working, and pricing a second limit at a fresher quote is
        # how the book of abandoned orders grew.
        blocked = held | pending
        regime = _regime()
        weak = weak_rows(args.weak_screen)
        ranked = _ranked_rows(args.screen)
        if args.direction == "auto" and regime == "RISK_OFF":
            # Down tape: the ranked top-N is all strength, so one bad day never
            # surfaces, and the weak tail is all weakness. Offer both - the
            # weakest momentum as puts, the ranked top by its own momentum as
            # calls - through the SAME gate (held + RSI + top) for each feed,
            # so a down tape that still contains a strong name is not filtered
            # out by the regime it happens to sit in.
            feed = "weak+ranked"
            puts = gated_candidates(None, args.screen_top, args.max_rsi,
                                    blocked, args.direction, rows=weak,
                                    force_side="put")
            calls = gated_candidates(args.screen, args.screen_top, args.max_rsi,
                                     blocked, args.direction)
            # Dedup by symbol, not by (symbol, side): a name carried by both
            # feeds must be offered once. The weak feed wins, so a down tape
            # prefers the put reading rather than accidentally buying a
            # straddle on the same underlying.
            offered = {symbol for symbol, _ in puts}
            candidates = puts + [c for c in calls if c[0] not in offered]
            _record_why(why, weak, "weak", "put")
            _record_why(why, ranked, "ranked", None)
        else:
            feed = "ranked"
            candidates = gated_candidates(args.screen, args.screen_top,
                                          args.max_rsi, blocked, args.direction)
            _record_why(why, ranked, "ranked", None)
        print(f"[entry] screen {screen_date} held={sorted(held)} "
              f"pending={sorted(pending)} regime={regime} feed={feed} "
              f"candidates={candidates[:args.max_tries]}")
    pilot_exec = "--execute" if args.execute else "--dry-run"
    for symbol, side in candidates[:args.max_tries]:
        reason = why.get(symbol, "no screen row (reason unavailable)")
        liquid_ok, liquid_reason = liquid_signal_filter(symbol, side,
                                                       args.liquid_signals)
        if not liquid_ok:
            print(f"[entry] skip {symbol} {side} -> {liquid_reason} | {reason}")
            continue
        # Zone confluence gate (POC + supply/demand), fail-open on any error.
        zone_note = "zone n/a"
        try:
            from scripts.zone_analysis import analyze

            wanted = "long" if side == "call" else "short"
            report = analyze(symbol, direction=wanted)
            code = report.get("verdict", {}).get("verdict", "neutral") \
                if "error" not in report else "neutral"
            if ((code == "long_blocked" and wanted == "long")
                    or (code == "short_blocked" and wanted == "short")):
                print(f"[entry] skip {symbol} {side} -> zone {code} | {reason}")
                continue
            if regime == "RISK_ON":
                confirmation = risk_on_confirmation(report, side)
                if confirmation:
                    print(f"[entry] skip {symbol} {side} -> {confirmation} | {reason}")
                    continue
            zone_note = f"zone {code}"
        except Exception:  # noqa: BLE001 - a zone bug never blocks the loop
            pass
        pilot_cmd = [py, "scripts/options_pilot.py",
                     "--ticker", symbol, "--direction", side,
                     "--premium-cap", str(args.premium_cap),
                     "--max-contracts", str(args.max_contracts),
                     "--max-positions", str(args.max_positions), pilot_exec]
        pilot_cmd.extend(["--max-iv", str(args.risk_off_max_iv)])
        rc, out, err = run(pilot_cmd)
        # The pilot's own JSON is the authoritative record of what it did.
        action = _pilot_action(out)
        detail = f"{reason} | {zone_note} | liquid ok | pilot {action}"
        # A refusal is plain text and lands on stderr, so without this the
        # window/premium refusals were invisible whenever the pilot had
        # already printed its spot line - which is exactly the "why" the log
        # exists to answer.
        if action == "no-json" and (err or "").strip():
            detail += f" (pilot stderr: {_tail(err)})"
        if '"action": "buy_placed"' in out:
            placed = f"{symbol} {side}"
            print(f"[entry] PLACED {placed} | {detail}")
            # The per-order Slack alert is best-effort, and the pilot now
            # records its outcome. On 2026-11-17 an order went in with no
            # alert and there was no way to tell afterwards whether Slack
            # had failed or the seam had never been reached. A failed alert
            # the order path must not raise, so the run log is the only
            # place it can surface.
            if '"alert": "failed"' in out.replace('": "', '":"'):
                print(f"[entry] ALERT FAILED for {symbol} {side} - order is "
                      f"in the ledger and at the broker, but no Slack message "
                      f"was delivered; check the bot token and channel")
            break
        if '"action": "dry_run_buy"' in out:
            placed = f"{symbol} {side} (dry-run)"
            print(f"[entry] would place {placed} | {detail}")
            break
        print(f"[entry] skip {symbol} {side} -> {_tail(out) or _tail(err)} "
              f"| {detail}")

    # 3. NOTIFY -- the watch that triggered this run. Only on a change: a loop
    # that Slack-messages every cycle is a loop nobody reads.
    if placed and not args.no_notify:
        # The notify report must use the cap the desk actually enforces, or
        # the close message disagrees with the loop that printed it: a
        # watchlist default once flagged an order "over cap" that
        # the watch itself (cap 500) would have accepted.
        rc, out, err = run([py, "scripts/options_watchlist.py", "--send",
                            "--premium-cap", str(args.premium_cap)])
        print(f"[notify] rc={rc} {_tail(out) or _tail(err)}")

    print(json.dumps({"exits_ran": not args.no_exits, "screen": screen_date,
                      "placed": placed, "executed": bool(args.execute),
                      "policy_version": POLICY.version, "paper_only": POLICY.paper_only}))
    return placed


def _parse_hhmm(text: str):
    return datetime.strptime(text, "%H:%M").time()


def open_order_underliers(env_file: str) -> set[str] | None:
    """Underliers that already have an unfilled order at the broker.

    Symmetric with `held_underliers`, and it closes the gap that let the loop
    stack one stale limit per cycle: the pilot's `--max-positions` check reads
    *positions*, and an unfilled order is not a position, so a working order
    was invisible to it. Without this, each cycle places a fresh limit at a
    fresh quote, nothing fills because the price has moved on, and the book
    of abandoned orders grows until the close.

    Fail-closed like `held_underliers`: None means "could not read", and the
    caller must block entries rather than pretend the book is clear.
    """
    try:
        import os

        from robinhood_tools.alpaca_paper import (
            AlpacaPaperBackend, AlpacaPaperHttpTransport)
        from robinhood_tools.options import occ_details
        from robinhood_tools.settings import load_env

        env = {**load_env(env_file), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        pending: set[str] = set()
        for order in backend.list_open_orders():
            symbol = str(order.get("symbol", ""))
            if len(symbol) <= 10:
                continue  # equity, not an OCC contract
            try:
                pending.add(occ_details(symbol).underlying)
            except Exception:  # noqa: BLE001 - an unparsable symbol is not a pass
                continue
        return pending
    except Exception as exc:  # noqa: BLE001 - unreadable means block, not pass
        print(f"[entry] open-order check FAILED ({type(exc).__name__}: {exc}); "
              "entries blocked this run (fail-closed)", file=sys.stderr)
        return None


def main(argv=None) -> int:
    args = parse_args(argv)
    # Line-buffer stdout: `>> log` makes Python block-buffer (8KB), so a loop
    # that runs all day would show nothing in its log until the buffer filled
    # - and would lose whatever was buffered if it were killed. The desk's
    # other loops (_log) already flush per line.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):  # pragma: no cover - very old Python
        pass
    py = desk_python()

    if not args.loop:
        _run_cycle(args, py)
        return 0

    # Loop mode: repeat the cycle every --loop minutes until --until ET. The
    # desk's window is 09:45-15:45 ET, so cycles outside it would only fail at
    # the pilot's session guard - we stop on the clock instead of guessing.
    stop_at = _parse_hhmm(args.until)
    cycles = 0
    while True:
        now_et = datetime.now(ET)
        if now_et.time() >= stop_at:
            print(f"[loop] stop: {now_et:%H:%M} ET >= {args.until}")
            break
        cycles += 1
        print(f"\n[loop] cycle {cycles} at {now_et:%H:%M:%S} ET "
              f"(every {args.loop}m until {args.until})")
        _run_cycle(args, py)
        # Sleep the interval, but never past --until: the next wake-up lands
        # exactly on the stop time, so the loop ends when it said it would
        # rather than lingering one interval late.
        now_et = datetime.now(ET)
        deadline = datetime.combine(now_et.date(), stop_at).replace(tzinfo=ET)
        remaining = (deadline - now_et).total_seconds()
        if remaining <= 0:
            print(f"[loop] stop: {now_et:%H:%M} ET >= {args.until}")
            break
        time.sleep(min(args.loop * 60, remaining))
    print(f"[loop] done, {cycles} cycle(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
