#!/usr/bin/env python3
"""Agent fleet: one autonomous agent per ticker, staggered in time.

Parallel LLM streams throttle the shared key (lesson learned 2026-10-08),
so agents run sequentially with a cooldown between them. Each agent owns
its tickers end-to-end (debate -> gates -> orders) and logs separately;
the fleet posts one combined Slack summary.

  ../TradingAgents/.venv/bin/python scripts/desk_fleet.py --agents NVDA,MRNA,TSLA --mode options
  ../TradingAgents/.venv/bin/python scripts/desk_fleet.py --agents NVDA,MRNA --mode stock --dry-run
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
TAVENV = Path("/Users/daniz/Desktop/2026work/TradingAgents/.venv/bin/python")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Run one agent per ticker, staggered.")
    p.add_argument("--agents", required=True, help="comma tickers, one agent each")
    p.add_argument("--mode", default="options", choices=["options", "stock"])
    p.add_argument("--stagger", type=int, default=120, help="seconds between agents")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--screen-top", type=int, default=0,
                   help="ignore --agents; take top-N from the screen instead")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    tickers = [t.strip().upper() for t in args.agents.split(",") if t.strip()]
    if args.screen_top > 0:
        try:
            screen = json.loads((ROOT / "outputs/screener/latest.json").read_text())
            tickers = [r["symbol"].upper().replace(".", "-")
                       for r in screen.get("ranked", [])[:args.screen_top]]
        except Exception as exc:
            print(f"screen unreadable ({type(exc).__name__}); aborting.")
            return 2
    if not tickers:
        print("no agents; nothing to do.")
        return 0

    import os

    env = {**os.environ,
           "SEC_EDGAR_USER_AGENT": os.environ.get("SEC_EDGAR_USER_AGENT",
                                                  "desk-fleet@localhost")}
    results = []
    for i, ticker in enumerate(tickers):
        if i > 0:
            print(f"stagger: waiting {args.stagger}s before agent {ticker}...")
            time.sleep(args.stagger)
        log = ROOT / f"outputs/fleet-{ticker}-{datetime.now(ET).strftime('%Y%m%d')}.log"
        cmd = [str(TAVENV if TAVENV.exists() else sys.executable),
               "scripts/tradingagents_paper_auto.py", "--tickers", ticker]
        if args.mode == "options":
            cmd.append("--options")
        if args.dry_run:
            cmd.append("--dry-run")
        print(f"agent {ticker}: {' '.join(cmd[-3:])} (log {log.name})")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== agent {ticker} start {datetime.now(ET).isoformat()} ===\n")
            proc = subprocess.run(cmd, cwd=ROOT, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True)
            fh.write(proc.stdout)
        rec = {"ticker": ticker, "returncode": proc.returncode}
        try:
            out_dir = ROOT / "outputs/paper/tradingagents" / ticker
            latest = sorted(out_dir.glob("*/auto.json"))[-1]
            rec.update(json.loads(latest.read_text()))
        except Exception as exc:
            rec["note"] = f"no auto.json ({type(exc).__name__})"
        results.append(rec)
        print(json.dumps({k: rec.get(k) for k in
                          ("ticker", "decision", "desk_verdict", "action", "reason")}))

    summary = {"date": datetime.now(ET).date().isoformat(), "agents": results}
    (ROOT / "outputs/fleet-summary.json").write_text(json.dumps(summary, indent=2))
    placed = sum(1 for r in results if str(r.get("action", "")).endswith("_placed"))
    line = (f"Fleet done: {len(results)} agents, {placed} placed, "
            + ", ".join(f"{r['ticker']}={r.get('action', '?')}" for r in results))
    print(line)
    try:
        sys.path.insert(0, ".")
        from scripts.notify import send

        send(f"*Fleet report*\n{line}")
    except Exception as exc:
        print(f"slack failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
