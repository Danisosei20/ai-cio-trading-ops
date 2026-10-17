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
  - S&P 500 constituent or ETF allowlist, premium <= cap, <=5 options,
    <=3 contracts, earnings blackout, session window -- all enforced
    downstream by options_pilot -> place_long_option. We never bypass.

  python3 scripts/options_watch_trade.py                        # dry-run (default)
  python3 scripts/options_watch_trade.py --execute
  python3 scripts/options_watch_trade.py --execute --no-exits   # entry only
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAVENV = Path("/Users/daniz/Desktop/2026work/TradingAgents/.venv/bin/python")


def desk_python() -> str:
    """Interpreter that has the desk's deps (yfinance, alpaca, ...).

    The trading-playbook notes the TradingAgents venv is the safe choice
    for background jobs; repo .venv may be missing deps.
    """
    return str(TAVENV) if TAVENV.exists() else sys.executable


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="15:10 options watch -> trade.")
    p.add_argument("--screen", default="outputs/screener/latest.json")
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
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def gated_candidates(screen_path: str | Path, top: int, max_rsi: float,
                     held: set[str], direction: str = "auto") -> list[tuple[str, str]]:
    """Pure: rank-ordered (symbol, direction) that clear the RSI/held gate."""
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
        if direction != "auto":
            side = direction
        else:
            mom = row.get("mom20")
            side = "call" if (mom is None or float(mom) >= 0) else "put"
        out.append((symbol, side))
    return out


def held_underliers(env_file: str) -> set[str]:
    """Underliers we already hold an OCC contract in (best effort)."""
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
    except Exception:
        return set()


def run(cmd: list[str]):
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
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
    candidates = gated_candidates(args.screen, args.screen_top, args.max_rsi,
                                  held, args.direction)
    print(f"[entry] screen {screen_date} held={sorted(held)} "
          f"candidates={candidates[:args.max_tries]}")
    pilot_exec = "--execute" if args.execute else "--dry-run"
    for symbol, side in candidates[:args.max_tries]:
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
