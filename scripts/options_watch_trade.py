#!/usr/bin/env python3
"""15:10 options watch -> trade. Owner 2026-10-14:

    "when it send the options watch, the bot should be buying and selling.
     the option calls, if the call lost 25% it should exit."

Deterministic, zero-burn (no LLM). One run does three things, in order:

  1. EXITS   options_guard.py --execute    take +2% / cut -25% / DTE / stale
  2. ENTRY   best gated screener name      one entry per run (buy)
  3. NOTIFY  options_watchlist.py --send   the watch itself

Entry gate (all deterministic, mirror of the trading-playbook):
  - candidate from the latest screener top-N, in rank order
  - RSI <= --max-rsi (default 73; playbook: <=~70 usable, avoid >75-77)
  - underlier not already held (no doubling a name)
  - direction follows 20d momentum (mom20 >= 0 -> call, else put)
  - regime: SPY below its 50-day mean (RISK_OFF) with --direction auto swaps
    the feed to the screener's weak-momentum tail (weak.json) and pins puts,
    because a one-day slide cannot flip a 20-day lookback and the ranked
    top-N only ever surfaces strength. Every other regime -- including
    UNKNOWN, which fails closed -- and an explicit --direction call/put keep
    the ranked feed unchanged.
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
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Run as `python scripts/options_watch_trade.py` puts scripts/ (not the repo
# root) on sys.path; robinhood_tools lives at the root. The fix has to run
# before the robinhood_tools import below, not after it.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robinhood_tools.market_prices import _spy_closes, market_regime  # noqa: E402

# TradingAgents venv interpreter. macOS source-machine path kept as default for
# backwards compatibility; set TRADINGAGENTS_VENV_PYTHON per machine (Windows:
# TradingAgents/.venv/Scripts/python.exe).
TAVENV = Path(os.environ.get(
    "TRADINGAGENTS_VENV_PYTHON",
    "/Users/daniz/Desktop/2026work/TradingAgents/.venv/bin/python"))


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
    p.add_argument("--premium-cap", type=Decimal, default=Decimal("3000"))
    p.add_argument("--max-contracts", type=int, default=5,
                   help="owner rule 2026-10-17: raised from 3 to 5")
    p.add_argument("--max-positions", type=int, default=5)
    p.add_argument("--max-tries", type=int, default=4,
                   help="candidates to attempt before giving up this run")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--no-exits", action="store_true")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--liquid-signals", default="outputs/liquid/latest.json",
                   help="Liquid signals file from Co-Invest MCP (signal gate only)")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


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
        return market_regime(_spy_closes())
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


def main(argv=None) -> int:
    args = parse_args(argv)
    py = desk_python()

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
    if held is None:
        candidates = []  # fail-closed: no entries without verified holdings
    else:
        regime = _regime()
        if args.direction == "auto" and regime == "RISK_OFF":
            # Down tape: the ranked top-N is all strength, so one bad day
            # never surfaces. Read the weakest-momentum tail as puts instead,
            # through the same gate below (held + RSI + top).
            feed = "weak"
            candidates = gated_candidates(None, args.screen_top, args.max_rsi,
                                          held, args.direction,
                                          rows=weak_rows(args.weak_screen),
                                          force_side="put")
        else:
            feed = "ranked"
            candidates = gated_candidates(args.screen, args.screen_top,
                                          args.max_rsi, held, args.direction)
        print(f"[entry] screen {screen_date} held={sorted(held)} "
              f"regime={regime} feed={feed} "
              f"candidates={candidates[:args.max_tries]}")
    pilot_exec = "--execute" if args.execute else "--dry-run"
    for symbol, side in candidates[:args.max_tries]:
        liquid_ok, liquid_reason = liquid_signal_filter(symbol, side,
                                                        args.liquid_signals)
        if not liquid_ok:
            print(f"[entry] skip {symbol} {side} -> {liquid_reason}")
            continue
        # Zone confluence gate (POC + supply/demand), fail-open on any error.
        try:
            from scripts.zone_analysis import analyze

            report = analyze(symbol)
            code = report.get("verdict", {}).get("verdict", "neutral") \
                if "error" not in report else "neutral"
            wanted = "long" if side == "call" else "short"
            if ((code == "long_blocked" and wanted == "long")
                    or (code == "short_blocked" and wanted == "short")):
                print(f"[entry] skip {symbol} {side} -> zone {code}")
                continue
        except Exception:  # noqa: BLE001 - a zone bug never blocks the loop
            pass
        rc, out, err = run([py, "scripts/options_pilot.py",
                            "--ticker", symbol, "--direction", side,
                            "--premium-cap", str(args.premium_cap),
                            "--max-contracts", str(args.max_contracts),
                            "--max-positions", str(args.max_positions),
                            pilot_exec])
        if '"action": "buy_placed"' in out:
            placed = f"{symbol} {side}"
            print(f"[entry] PLACED {placed}")
            break
        if '"action": "dry_run_buy"' in out:
            placed = f"{symbol} {side} (dry-run)"
            print(f"[entry] would place {placed}")
            break
        print(f"[entry] skip {symbol} {side} -> {_tail(out) or _tail(err)}")

    # 3. NOTIFY -- the watch that triggered this run.
    if not args.no_notify:
        rc, out, err = run([py, "scripts/options_watchlist.py", "--send"])
        print(f"[notify] rc={rc} {_tail(out) or _tail(err)}")

    print(json.dumps({"exits_ran": not args.no_exits, "screen": screen_date,
                      "placed": placed, "executed": bool(args.execute)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
