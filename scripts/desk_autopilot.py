#!/usr/bin/env python3
"""Desk autopilot: one entry point for every scheduled session.

Replaces remembering five scripts and their flags. Each mode enforces its
own session window through the gated flows; outside hours it reports and
exits without placing.

  python3 scripts/desk_autopilot.py morning   # screen top-2 -> debates -> options entries
  python3 scripts/desk_autopilot.py guard     # enforce stops/targets on all positions
  python3 scripts/desk_autopilot.py learn     # settle due checkpoints
  python3 scripts/desk_autopilot.py close     # 15:10 watchlist to Slack
  python3 scripts/desk_autopilot.py status    # book + schedules + health snapshot
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAVENV = Path("/Users/daniz/Desktop/2026work/TradingAgents/.venv/bin/python")


def run(cmd: list[str], env_extra: dict | None = None) -> int:
    import os

    env = {**os.environ, **(env_extra or {})}
    proc = subprocess.run(cmd, cwd=ROOT, env=env)
    return proc.returncode


def pick_python(*, needs_llm: bool = False) -> str:
    if needs_llm and TAVENV.exists():
        return str(TAVENV)
    return sys.executable


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Desk autopilot sessions.")
    ap.add_argument("mode", choices=["morning", "guard", "learn", "close", "status"])
    a = ap.parse_args(argv)

    if a.mode == "morning":
        import os

        env = {"SEC_EDGAR_USER_AGENT": os.environ.get(
            "SEC_EDGAR_USER_AGENT", "desk-autopilot@localhost")}
        return run([pick_python(needs_llm=True),
                    "scripts/tradingagents_paper_auto.py",
                    "--screen", "outputs/screener/latest.json",
                    "--screen-top", "2", "--options"], env_extra=env)
    if a.mode == "guard":
        code = run([sys.executable, "scripts/paper_position_guard.py", "--execute"])
        run([sys.executable, "scripts/options_guard.py", "--execute"])
        return code
    if a.mode == "learn":
        return run([sys.executable, "scripts/learn_from_trades.py"])
    if a.mode == "close":
        return run([pick_python(needs_llm=True),
                    "scripts/options_watchlist.py", "--send"])
    if a.mode == "status":
        return run([sys.executable, "scripts/morning_check.py"])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
