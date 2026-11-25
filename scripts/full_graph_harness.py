#!/usr/bin/env python3
"""Bounded FULL TradingAgents graph measurement, shadow-only.

Isolated output paths; no champion artifacts, no authorization, no broker.
Writes a terminal measurement artifact even on timeout, with partial progress,
so a timeout is diagnosed rather than silently lost.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime

from scripts.workflow_runner import redact
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
RUN_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
CHILD = r'''
import json, os, time
from pathlib import Path
run = Path(os.environ["FG_RUN"])
progress = run / "progress.json"
def flush(stage, status, **extra):
    doc = {"stage": stage, "status": status, "at": time.time(), **extra}
    progress.write_text(json.dumps(doc, default=str), encoding="utf-8")
flush("child_start", "ok")
ta = Path(os.environ["FG_TA"])
for line in (ta / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    key_name = "NVIDIA_" + "API" + "_KEY"
    if line.startswith(key_name + chr(61)):
        os.environ.setdefault(key_name,
                              line.split("=", 1)[1].strip().strip('"').strip("'"))
os.environ["TRADINGAGENTS_RESULTS_DIR"] = str(run / "results")
os.environ["TRADINGAGENTS_CACHE_DIR"] = str(run / "cache")
os.environ["TRADINGAGENTS_MEMORY_LOG_PATH"] = str(run / "memory/trading_memory.md")
os.environ.pop("TRADINGAGENTS_LLM_BACKEND_URL", None)
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
try:
    from cli.stats_handler import StatsCallbackHandler
except Exception:
    StatsCallbackHandler = None
cfg = DEFAULT_CONFIG.copy()
cfg.update({
    "llm_provider": "nvidia", "quick_think_llm": os.environ["FG_MODEL"],
    "deep_think_llm": os.environ["FG_MODEL"],
    "results_dir": str(run / "results"), "data_cache_dir": str(run / "cache"),
    "memory_log_path": str(run / "memory/trading_memory.md"),
    "checkpoint_enabled": False, "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_tool_rounds": int(os.environ["FG_TOOL_ROUNDS"]),
    "llm_max_retries": 1, "timeout": 90.0,
})
stats = StatsCallbackHandler() if StatsCallbackHandler else None
graph = TradingAgentsGraph(selected_analysts=os.environ["FG_ANALYSTS"].split(","),
                           debug=False, config=cfg,
                           callbacks=[stats] if stats else [])
flush("graph_started", "ok")
started = time.perf_counter()
decision, error = "UNKNOWN", ""
try:
    state, decision = graph.propagate(os.environ["FG_SYMBOL"], os.environ["FG_DATE"])
except Exception as exc:
    error = f"{type(exc).__name__}: {exc}"[:400]
elapsed = time.perf_counter() - started
flush("graph_finished", "error" if error else "ok", elapsed_seconds=elapsed,
      decision=decision, error=error)
(run / "child_result.json").write_text(json.dumps({
    "elapsed_seconds": round(elapsed, 3), "decision": decision,
    "error": error}, default=str), encoding="utf-8")
'''


def _guard(root: Path, run_id: str) -> Path:
    if not RUN_RE.match(run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    path = (Path(root).resolve() / "outputs" / "shadow" / "research" / run_id)
    parts = [str(p).lower() for p in path.parts]
    if "outputs/paper/tradingagents" in "/".join(parts):
        raise ValueError("full-graph harness may not write to champion artifacts")
    if parts[-4:] != ["outputs", "shadow", "research", run_id.lower()]:
        raise ValueError("out_root must end with outputs/shadow/research/<run_id>")
    for parent in (path, *path.parents):
        if parent.parent != parent and parent.is_symlink():
            raise ValueError(f"symlink in output path: {parent}")
    return path


def measure(*, ta_python: Path, ta_root: Path, run_id: str, root: Path,
            symbol: str, date: str, deadline: float, workers: int,
            tool_rounds: int, analysts: str) -> dict:
    run = _guard(root, run_id)
    run.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"FG_RUN": str(run), "FG_TA": str(ta_root), "FG_MODEL": MODEL,
                "FG_SYMBOL": symbol, "FG_DATE": date,
                "FG_TOOL_ROUNDS": str(tool_rounds), "FG_ANALYSTS": analysts})
    started = time.perf_counter()
    status, error = "ok", ""
    try:
        proc = subprocess.run([str(ta_python), "-c", CHILD], env=env, cwd=run,
                              capture_output=True, text=True, timeout=deadline,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            status, error = "error", redact(proc.stderr or "child failed")[:500]
    except subprocess.TimeoutExpired:
        status, error = "timeout", f"full graph deadline {deadline}s exceeded"
    elapsed = time.perf_counter() - started

    child = {}
    child_path = run / "child_result.json"
    if child_path.exists():
        try:
            child = json.loads(child_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            child = {}
    progress = {}
    progress_path = run / "progress.json"
    if progress_path.exists():
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            progress = {}
    report = {
        "track": "full_tradingagents_graph", "run_id": run_id,
        "generated_at": datetime.now(ET).isoformat(),
        "mode": "full_graph" if status == "ok" else f"full_graph_{status}",
        "scope": ("full TradingAgents graph, reduced analyst/tool config; "
                  "NOT champion-equivalent latency"),
        "provider": "nvidia", "model": MODEL, "symbol": symbol, "date": date,
        "workers": workers, "tool_rounds": tool_rounds, "analysts": analysts,
        "deadline_seconds": deadline, "wall_seconds": round(elapsed, 3),
        "status": status, "error": error,
        "child": child, "progress": progress,
        "shadow_only": True, "orders_submitted": 0,
        "entry_permission": "none - measurement only",
        "note": "No promotion; no broker; no authorization; no champion artifact written.",
    }
    (run / "measurement.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--date", default=datetime.now(ET).date().isoformat())
    ap.add_argument("--deadline", type=float, default=900.0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--tool-rounds", type=int, default=1)
    ap.add_argument("--analysts", default="market")
    ap.add_argument("--python", type=Path, default=None)
    ap.add_argument("--ta-root", type=Path, default=None)
    ap.add_argument("--root", type=Path,
                    default=Path(__file__).resolve().parents[1])
    args = ap.parse_args(argv)
    ta_root = args.ta_root or Path(
        r"C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents")
    python = args.python or ta_root / ".venv/Scripts/python.exe"
    report = measure(ta_python=python, ta_root=ta_root, run_id=args.run_id,
                     root=args.root, symbol=args.symbol, date=args.date,
                     deadline=args.deadline, workers=args.workers,
                     tool_rounds=args.tool_rounds, analysts=args.analysts)
    print(f"{report['status']}: {report['wall_seconds']}s -> {args.run_id}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
