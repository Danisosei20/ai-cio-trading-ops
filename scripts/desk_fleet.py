#!/usr/bin/env python3
"""Agent fleet: agents that do multiple tasks.

Two modes:

1. Per-ticker mode (default, unchanged): one autonomous agent per ticker,
   staggered in time. Parallel LLM streams throttle the shared key (lesson
   learned 2026-10-08), so agents run sequentially with a cooldown. Each agent
   owns its tickers end-to-end (debate -> gates -> orders) and logs separately;
   the fleet posts one combined Slack summary.

2. Task mode (--tasks): a pipeline of task-agents, each doing a *different*
   job and feeding the next through a shared context:

       screen  ->  research  ->  verify  ->  report

   Task mode is OBSERVE/RESEARCH ONLY: it never places orders. That is
   deliberate -- it is safe to schedule even while the desk is parked.

  ../TradingAgents/.venv/bin/python scripts/desk_fleet.py --agents NVDA,MRNA,TSLA --mode options
  ../TradingAgents/.venv/bin/python scripts/desk_fleet.py --agents NVDA,MRNA --mode stock --dry-run
  ../TradingAgents/.venv/bin/python scripts/desk_fleet.py --tasks screen,research,verify,report --task-top 3
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
TAVENV = Path("/Users/daniz/Desktop/2026work/TradingAgents/.venv/bin/python")

PRESETS = {
    "scan": "screen,verify,report",
    "morning": "screen,research,verify,report",
    "research-lite": "screen,research,verify,report",
    "research": "screen,liquid,research,news,verify,report",
    "earnings-desk": "screen,earnings,research,verify,report",
    "liquid-desk": "screen,liquid,research,verify,report",
}


def _python() -> str:
    return str(TAVENV if TAVENV.exists() else sys.executable)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Run one agent per ticker, staggered.")
    p.add_argument("--agents", help="comma tickers, one agent each")
    p.add_argument("--mode", default="options", choices=["options", "stock"])
    p.add_argument("--stagger", type=int, default=120, help="seconds between agents")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--screen-top", type=int, default=0,
                   help="ignore --agents; take top-N from the screen instead")
    # task mode
    p.add_argument("--tasks", default="",
                   help="task mode: comma list, e.g. screen,research,verify,report")
    p.add_argument("--task-top", type=int, default=3,
                   help="candidates passed to the research/news tasks")
    p.add_argument("--cooldown", type=int, default=5,
                   help="seconds between tasks (task mode)")
    p.add_argument("--no-notify", action="store_true", help="skip the Slack post")
    p.add_argument("--plan", default="", choices=sorted(PRESETS),
                   help="named task preset: " + ", ".join(sorted(PRESETS)))
    return p.parse_args(argv)


# --------------------------------------------------------------------------
# Task mode
# --------------------------------------------------------------------------

def _run(ctx: dict, name: str, cmd: list[str], timeout: int = 1800):
    """Run one subprocess, tee its output to a per-task log."""
    log = ctx["log_dir"] / f"{name}.log"
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n=== {name} start {datetime.now(ET).isoformat()} ===\n")
        fh.write("$ " + " ".join(cmd) + "\n")
        fh.flush()
        proc = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=timeout)
        fh.write(proc.stdout or "")
    return proc


def task_screen(ctx: dict, args) -> dict:
    """Agent 1 (deterministic): run the screener, publish today's shortlist."""
    out = ROOT / "outputs/screener/latest.json"
    cmd = [_python(), "scripts/universe_screen.py",
           "--top", str(max(args.task_top, 1) * 5), "--out", str(out)]
    proc = _run(ctx, "screen", cmd)
    tickers: list[str] = []
    if out.exists():
        try:
            data = json.loads(out.read_text())
            tickers = [str(r["symbol"]).upper().replace(".", "-")
                       for r in data.get("ranked", [])]
        except Exception as exc:  # noqa: BLE001 - report, never crash the fleet
            ctx["notes"].append(f"screen parse failed: {type(exc).__name__}")
    ctx["tickers"] = tickers
    return {"returncode": proc.returncode, "count": len(tickers), "tickers": tickers}


def _research(ctx: dict, args, name: str, extra: list[str], tag: str) -> dict:
    date = datetime.now(ET).date().isoformat()
    wanted = ctx.get("tickers", [])[: args.task_top]
    # Respect the Earnings department blackout when it ran earlier in the plan.
    earned = ctx.get("results", {}).get("earnings", {}).get("statuses", {})
    if earned:
        wanted = [t for t in wanted if earned.get(t) != "blacked_out"]
        skipped = [t for t in ctx.get("tickers", [])[: args.task_top]
                   if earned.get(t) == "blacked_out"]
        if skipped:
            ctx["notes"].append(f"{name}: skipped blacked-out {', '.join(skipped)}")
    # Respect Liquid signals — skip crowded longs where smart money is fading.
    liquid = ctx.get("results", {}).get("liquid", {})
    if liquid:
        avoid = set(liquid.get("crowded_longs", []))
        skipped = [t for t in wanted if t in avoid]
        wanted = [t for t in wanted if t not in avoid]
        if skipped:
            ctx["notes"].append(f"{name}: skipped liquid-crowded {', '.join(skipped)}")
    runs = []
    for i, t in enumerate(wanted):
        if i > 0 and args.cooldown:
            time.sleep(args.cooldown)
        cmd = [_python(), "scripts/tradingagents_research.py",
               "--ticker", t, "--date", date, *extra]
        proc = _run(ctx, f"{tag}-{t}", cmd)
        runs.append({"ticker": t, "returncode": proc.returncode})
    if not wanted:
        ctx["notes"].append(f"{name}: no tickers (run the screen task first)")
    return {"tickers": wanted, "runs": runs}


def task_research(ctx: dict, args) -> dict:
    """Agent 2 (LLM, research only): full debate per candidate."""
    return _research(ctx, args, "research", [], "research")


def task_news(ctx: dict, args) -> dict:
    """Agent 3 (LLM, research only): news analyst per candidate."""
    return _research(ctx, args, "news", ["--analysts", "news"], "news")


def task_liquid(ctx: dict, args) -> dict:
    """Agent: Liquid signal department — read-only market intelligence from
    Co-Invest MCP. News, unusual activity, positioning pulse (smart money vs
    crowd), and leaderboard of profitable traders. Never places trades."""
    import json

    signals_path = ROOT / "outputs/liquid" / "latest.json"
    data = {}
    if signals_path.exists():
        try:
            data = json.loads(signals_path.read_text())
        except (json.JSONDecodeError, OSError):
            ctx["notes"].append("liquid: signals file unreadable")
    if not data:
        ctx["notes"].append("liquid: no signals file — run liquid_signals refresh")
        return {"signals": {}, "crowded_longs": [], "crowded_shorts": [],
                "leaderboard": [], "positioning": {}, "news": {},
                "note": "no liquid signals file"}
    signals = data.get("signals", {})
    positioning = data.get("positioning", {})
    leaderboard = data.get("leaderboard", [])
    crowded_longs = [s["symbol"] for s in positioning.get("long_heavy_crowd", [])]
    crowded_shorts = [s["symbol"] for s in positioning.get("short_heavy_crowd", [])]
    ctx["notes"].append(
        f"liquid: {len(crowded_longs)} crowded longs / {len(crowded_shorts)} crowded shorts "
        f"| {len(leaderboard)} leaders tracked"
    )
    return {
        "signals": signals,
        "crowded_longs": crowded_longs,
        "crowded_shorts": crowded_shorts,
        "leaderboard": leaderboard[:5],
        "positioning": positioning,
        "news": data.get("news", {}),
        "updated_at": data.get("updated_at"),
    }


def task_earnings(ctx: dict, args) -> dict:
    """Agent: Earnings department — enforces the 5-day earnings blackout.

    Reads the optional earnings cache (outputs/earnings/latest.json,
    shape {symbol: "YYYY-MM-DD"}) and flags any candidate whose next
    earnings fall inside the blackout window. Safe default: unknown
    earnings are *not* blocked — the desk never trades on a guess.
    """
    from datetime import date, timedelta

    today = date.today()
    blackout_until = today + timedelta(days=5)
    cache = ROOT / "outputs/earnings" / "latest.json"
    data = {}
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            ctx["notes"].append("earnings: cache unreadable — treating all as unknown")

    statuses: dict[str, str] = {}
    blacked: list[str] = []
    for t in ctx.get("tickers", []):
        raw = data.get(t)
        if not raw:
            statuses[t] = "unknown"
            continue
        try:
            edate = date.fromisoformat(str(raw))
        except ValueError:
            statuses[t] = "unknown"
            continue
        if today <= edate <= blackout_until:
            statuses[t] = "blacked_out"
            blacked.append(t)
        else:
            statuses[t] = "clear"

    if blacked:
        ctx["notes"].append(
            "earnings blackout: " + ", ".join(blacked) +
            " earning within 5 days — held out of research"
        )
    return {"tickers": ctx.get("tickers", []), "statuses": statuses,
            "blacked_out": blacked, "window_days": 5}


def task_verify(ctx: dict, args) -> dict:
    """Agent 4 (deterministic): check the inputs are fresh and usable."""
    checks: dict = {}
    screen = ROOT / "outputs/screener/latest.json"
    if screen.exists():
        age_h = (time.time() - screen.stat().st_mtime) / 3600
        checks["screener_fresh"] = age_h < 24
        checks["screener_age_hours"] = round(age_h, 1)
    else:
        checks["screener_fresh"] = False
        checks["screener_age_hours"] = None
    checks["tradingagents_venv"] = TAVENV.exists()
    checks["has_tickers"] = bool(ctx.get("tickers"))
    ok = all(v for v in checks.values() if isinstance(v, bool))
    if not ok:
        ctx["notes"].append("verify: one or more checks failed")
    return {"ok": ok, "checks": checks}


def task_report(ctx: dict, args) -> dict:
    """Agent 5: merge everything into one summary (+ Slack)."""
    summary = {
        "date": datetime.now(ET).date().isoformat(),
        "mode": "tasks",
        "tasks": ctx.get("planned") or list(ctx["results"].keys()),
        "tickers": ctx.get("tickers", []),
        "results": ctx["results"],
        "notes": ctx["notes"],
        "observe_only": True,
        "log_dir": str(ctx["log_dir"]),
    }
    path = ROOT / "outputs/fleet-summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str))
    line = (f"Fleet tasks: {', '.join(summary['tasks'])} | "
            f"tickers={len(summary['tickers'])} | notes={len(summary['notes'])}")
    for n in ctx["notes"]:
        line += f"\n- {n}"
    if not args.no_notify:
        try:
            from scripts.notify import send
            send(f"*Fleet report*\n{line}")
        except Exception as exc:  # noqa: BLE001
            print(f"slack failed: {exc}")
    return {"summary": str(path), "line": line}


TASKS = {
    "screen": task_screen,
    "research": task_research,
    "news": task_news,
    "liquid": task_liquid,
    "earnings": task_earnings,
    "verify": task_verify,
    "report": task_report,
}


def _write_task_json(ctx: dict, task: str, payload: dict) -> None:
    ts = datetime.now(ET).strftime("%Y%m%d_%H%M%S")
    out_dir = ctx["log_dir"]
    path = out_dir / f"{task}_{ts}.json"
    try:
        path.write_text(json.dumps(payload, indent=2, default=str))
    except Exception:
        pass
    # also write latest for convenience
    latest = out_dir / f"{task}.json"
    try:
        latest.write_text(json.dumps(payload, indent=2, default=str))
    except Exception:
        pass


def run_tasks(args) -> int:
    task_list = args.tasks
    if args.plan:
        task_list = PRESETS[args.plan]
    names = [t.strip().lower() for t in task_list.split(",") if t.strip()]
    unknown = [n for n in names if n not in TASKS]
    if unknown:
        print(f"unknown task(s): {','.join(unknown)}")
        print(f"known: {','.join(TASKS)}")
        return 2
    stamp = datetime.now(ET).strftime("%Y%m%d_%H%M%S")
    ctx = {"date": datetime.now(ET).date().isoformat(), "tickers": [],
           "results": {}, "notes": [], "planned": names,
           "log_dir": ROOT / "outputs/fleet" / stamp}
    ctx["log_dir"].mkdir(parents=True, exist_ok=True)
    print(f"task fleet -> {ctx['log_dir']}")
    from scripts.office_state import outcome, publish
    statuses = {n: "queued" for n in names}
    stop = threading.Event()
    lock = threading.Lock()

    def update(finished=False):
        with lock:
            try:
                publish(ROOT, stamp, dict(statuses), finished=finished)
            except OSError as exc:
                print(f"office status unavailable: {exc}")

    def heartbeat():
        while not stop.wait(5):
            update()

    update()
    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        for i, n in enumerate(names):
            if i > 0 and args.cooldown:
                time.sleep(args.cooldown)
            with lock:
                statuses[n] = "working"
            update()
            print(f"task {n} ...")
            try:
                res = TASKS[n](ctx, args)
            except BaseException:
                with lock:
                    statuses[n] = "blocked"
                raise
            ctx["results"][n] = res
            _write_task_json(ctx, n, res)
            with lock:
                statuses[n] = outcome(res)
            update()
            print(json.dumps({n: res}, default=str)[:400])
    finally:
        stop.set()
        worker.join()
        update(finished=True)
    # symlink latest summary
    try:
        (ROOT / "outputs/fleet-latest.json").write_text(
            (ROOT / "outputs/fleet-summary.json").read_text()
        )
    except Exception:
        pass
    return 0


# --------------------------------------------------------------------------
# Per-ticker mode
# --------------------------------------------------------------------------

def run_agents(args) -> int:
    tickers = [t.strip().upper() for t in (args.agents or "").split(",") if t.strip()]
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
        cmd = [_python(), "scripts/tradingagents_paper_auto.py", "--tickers", ticker]
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
    if not args.no_notify:
        try:
            sys.path.insert(0, ".")
            from scripts.notify import send

            send(f"*Fleet report*\n{line}")
        except Exception as exc:
            print(f"slack failed: {exc}")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.plan or args.tasks:
        return run_tasks(args)
    return run_agents(args)


if __name__ == "__main__":
    raise SystemExit(main())
