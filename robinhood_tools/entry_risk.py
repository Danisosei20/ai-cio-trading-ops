#!/usr/bin/env python3
"""Unified, deterministic ENTRY risk interface (ADR 0002, approved).

Both existing kill switches are consulted (DB emergency flag via caller and the
file flag on disk), daily and weekly drawdowns use explicit ET-session baselines,
and the all-time-peak figure is kept only as a secondary diagnostic. Exit /
flatten / cover actions are explicitly exempt from entry gating but remain
subject to broker review and reconciliation elsewhere.
"""
from __future__ import annotations

from pathlib import Path

DAILY_DRAWDOWN_LIMIT = 0.03
WEEKLY_DRAWDOWN_LIMIT = 0.07
KILL_FILE = "outputs/paper/KILL_SWITCH"


def file_kill_switch(root: Path) -> bool:
    return (Path(root) / KILL_FILE).exists()


def kill_switch_engaged(root: Path, *, db_kill: bool = False) -> bool:
    """True when EITHER authoritative entry kill mechanism is engaged."""
    return bool(db_kill) or file_kill_switch(root)


def drawdowns(*, current_equity: float, day_start_equity: float,
              week_start_equity: float, peak_equity: float) -> dict:
    """Return three distinct drawdown measures with explicit baselines."""
    def drop(baseline: float) -> float:
        if baseline <= 0:
            return 0.0
        return max(0.0, (baseline - current_equity) / baseline)
    return {
        "daily_pct": drop(day_start_equity),
        "weekly_pct": drop(week_start_equity),
        "alltime_pct": drop(peak_equity),
        "current_equity": current_equity,
        "baselines": {"day": day_start_equity, "week": week_start_equity,
                      "peak": peak_equity},
    }


def entry_permitted(root: Path, *, is_exit: bool, db_kill: bool,
                    current_equity: float, day_start_equity: float,
                    week_start_equity: float, peak_equity: float) -> tuple[bool, str]:
    """Single entry gate: fail closed on unknown equity or breached risk."""
    if is_exit:
        return True, "exit exempt from entry risk gate"
    if kill_switch_engaged(root, db_kill=db_kill):
        return False, "entry kill switch engaged"
    if current_equity <= 0 or day_start_equity <= 0 or week_start_equity <= 0:
        return False, "equity baseline unavailable"
    d = drawdowns(current_equity=current_equity,
                  day_start_equity=day_start_equity,
                  week_start_equity=week_start_equity, peak_equity=peak_equity)
    if d["daily_pct"] > DAILY_DRAWDOWN_LIMIT:
        return False, f"daily drawdown limit breached ({d['daily_pct']:.2%})"
    if d["weekly_pct"] > WEEKLY_DRAWDOWN_LIMIT:
        return False, f"weekly drawdown limit breached ({d['weekly_pct']:.2%})"
    return True, "entry risk ok"
