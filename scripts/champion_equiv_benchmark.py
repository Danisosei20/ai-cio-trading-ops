#!/usr/bin/env python3
"""Bounded CHAMPION-EQUIVALENT TradingAgents graph benchmark (shadow-only).

The configuration is a frozen copy of the live operational champion
(`tradingagents_paper_auto.run_research` plus `intraday_loop` judge overrides):
market+news analysts, 4 tool rounds, 1 debate round, 1 risk round, NVIDIA NIM,
quick=deep=nemotron, 90 s provider timeout, 1 retry. It never writes champion
artifacts, never imports the execution path, never touches a broker, and
submits no orders.

A terminal measurement artifact is written on success, error or timeout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.workflow_runner import redact  # noqa: E402

ET = ZoneInfo("America/New_York")
MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
RUN_RE = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
CHAMPION_CONFIG = {
    "llm_provider": "nvidia",
    "quick_think_llm": MODEL,
    "deep_think_llm": MODEL,
    "selected_analysts": ["market", "news"],
    "max_tool_rounds": 4,
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "llm_max_retries": 1,
    "timeout": 90.0,
    "checkpoint_enabled": False,
    "max_recur_limit": 100,
}
CONFIG_DIGEST = hashlib.sha256(json.dumps(
    CHAMPION_CONFIG, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

CHILD = r'''
import json, os, time, hashlib
from pathlib import Path
run = Path(os.environ["CE_RUN"])
progress = run / "progress.json"
counts = {"llm_calls": 0, "tool_calls": 0, "llm_errors": 0, "retries": 0}
def flush(stage, status, **extra):
    doc = {"stage": stage, "status": status, "at": time.time(), "counts": dict(counts), **extra}
    progress.write_text(json.dumps(doc, default=str), encoding="utf-8")
flush("child_start", "ok")
ta = Path(os.environ["CE_TA"])
for line in (ta / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    if line.startswith("NVIDIA_" + "API" + "_KEY="):
        os.environ.setdefault("NVIDIA_" + "API" + "_KEY",
                              line.split("=", 1)[1].strip().strip('"').strip("'"))
# Pin every TRADINGAGENTS_* override so "same config" is provable.
for key in list(os.environ):
    if key.startswith("TRADINGAGENTS_"):
        os.environ.pop(key, None)
os.environ["TRADINGAGENTS_RESULTS_DIR"] = str(run / "results")
os.environ["TRADINGAGENTS_CACHE_DIR"] = str(run / "cache")
os.environ["TRADINGAGENTS_MEMORY_LOG_PATH"] = str(run / "memory/trading_memory.md")
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
cfg = DEFAULT_CONFIG.copy()
cfg.update({
    "llm_provider": "nvidia",
    "quick_think_llm": os.environ["CE_MODEL"],
    "deep_think_llm": os.environ["CE_MODEL"],
    "results_dir": str(run / "results"),
    "data_cache_dir": str(run / "cache"),
    "memory_log_path": str(run / "memory/trading_memory.md"),
    "checkpoint_enabled": False,
    "max_debate_rounds": 1, "max_risk_discuss_rounds": 1,
    "max_tool_rounds": 4, "llm_max_retries": 1, "timeout": 90.0,
})
actual = {k: cfg.get(k) for k in (
    "llm_provider", "quick_think_llm", "deep_think_llm", "max_tool_rounds",
    "max_debate_rounds", "max_risk_discuss_rounds", "llm_max_retries", "timeout")}
expected = json.loads(os.environ["CE_CONFIG"])
digest = hashlib.sha256(json.dumps(expected, sort_keys=True,
    separators=(",", ":")).encode()).hexdigest()
keys = [k for k in expected if k != "selected_analysts"]
config_match = all(cfg.get(k) == expected[k] for k in keys)
flush("config_frozen", "ok" if config_match else "mismatch",
      digest=digest, actual=actual, config_match=config_match)
from langchain_core.callbacks import BaseCallbackHandler
class Counter(BaseCallbackHandler):
    def on_llm_start(self, *a, **kw):
        counts["llm_calls"] += 1
    def on_llm_end(self, *a, **kw):
        pass
    def on_llm_error(self, *a, **kw):
        counts["llm_errors"] += 1
    def on_tool_start(self, *a, **kw):
        counts["tool_calls"] += 1
    def on_tool_end(self, *a, **kw):
        pass
    def on_tool_error(self, *a, **kw):
        counts["llm_errors"] += 1
graph = TradingAgentsGraph(
    selected_analysts=["market", "news"], debug=False, config=cfg,
    callbacks=[Counter()])
flush("graph_started", "ok")
t0 = time.perf_counter()
decision, error, state = "UNKNOWN", "", {}
try:
    state, decision = graph.propagate(os.environ["CE_SYMBOL"], os.environ["CE_DATE"])
except Exception as exc:
    error = f"{type(exc).__name__}: {exc}"[:500]
elapsed = time.perf_counter() - t0
reports = {k: bool(state.get(k)) for k in ("market_report", "news_report")}
rating = ""
try:
    from tradingagents.agents.rating import run_rating
    rating = str(run_rating(state))[:60]
except Exception:
    pass
flush("graph_finished", "error" if error else "ok", elapsed_seconds=elapsed,
      decision=decision, reports=reports, rating=rating, error=error)
(run / "child_result.json").write_text(json.dumps({
    "elapsed_seconds": round(elapsed, 3), "graph_seconds": round(elapsed, 3),
    "decision": decision, "error": error, "reports": reports,
    "rating": rating, "counts": counts, "config_digest": digest,
    "config_actual": actual, "config_match": config_match},
    default=str), encoding="utf-8")
'''


def _guard(root: Path, run_id: str) -> Path:
    if not RUN_RE.match(run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    path = (Path(root).resolve() / "outputs" / "shadow" / "research" / run_id)
    parts = [str(p).lower() for p in path.parts]
    if "outputs/paper/tradingagents" in "/".join(parts):
        raise ValueError("benchmark may not write champion artifacts")
    if parts[-4:] != ["outputs", "shadow", "research", run_id.lower()]:
        raise ValueError("out_root must end with outputs/shadow/research/<run_id>")
    for parent in (path, *path.parents):
        if parent.parent != parent and parent.is_symlink():
            raise ValueError(f"symlink in output path: {parent}")
    return path


def _kill_tree(proc: subprocess.Popen) -> str:
    """Terminate only the child tree this benchmark created."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=20)
        else:
            proc.terminate()
    except Exception as exc:  # noqa: BLE001
        return f"cleanup failed: {type(exc).__name__}: {exc}"
    return "child tree terminated"


def measure(*, ta_python: Path, ta_root: Path, run_id: str, root: Path,
            symbol: str, date: str, deadline: float) -> dict:
    run = _guard(root, run_id)
    run.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"CE_RUN": str(run), "CE_TA": str(ta_root), "CE_MODEL": MODEL,
                "CE_SYMBOL": symbol, "CE_DATE": date,
                "CE_CONFIG": json.dumps(CHAMPION_CONFIG, sort_keys=True,
                                        separators=(",", ":"))})
    started = time.perf_counter()
    status, error, cleanup = "ok", "", ""
    proc = None
    try:
        proc = subprocess.Popen(
            [str(ta_python), "-c", CHILD], env=env, cwd=run,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            encoding="utf-8", errors="replace")
        out, err = proc.communicate(timeout=deadline)
        if proc.returncode != 0:
            status, error = "error", redact(err or "child failed")[:4000]
    except subprocess.TimeoutExpired:
        status, error = "timeout", f"champion-equivalent deadline {deadline}s exceeded"
        if proc is not None:
            cleanup = _kill_tree(proc)
            try:
                _, err = proc.communicate(timeout=20)
            except Exception:  # noqa: BLE001
                err = ""
    except Exception as exc:  # noqa: BLE001
        status, error = "error", f"{type(exc).__name__}: {exc}"
        err = ""
    elapsed = time.perf_counter() - started

    def load(name: str) -> dict:
        path = run / name
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return {}

    child = load("child_result.json")
    progress = load("progress.json")
    reports = child.get("reports") or {}
    evidence_complete = bool(reports.get("market_report") and
                             reports.get("news_report")) and status == "ok"
    if status == "ok" and child.get("error"):
        status, error = "error", f"child graph error: {child['error']}"[:600]
    if status == "ok" and not child.get("config_match", False):
        status, error = "error", "frozen champion config did not match runtime config"
    if status == "ok" and not evidence_complete:
        status, error = "error", "evidence incomplete: missing analyst reports"
    if status != "ok":
        evidence_complete = False
    report = {
        "track": "champion_equivalent_tradingagents_graph",
        "run_id": run_id, "generated_at": datetime.now(ET).isoformat(),
        "mode": "champion_equivalent" if status == "ok" else f"champion_equivalent_{status}",
        "scope": ("frozen live champion config: market+news, 4 tool rounds, "
                  "1 debate/risk round, NVIDIA NIM; isolated graph only, "
                  "no downstream desk gates, retry re-roll or save_reports"),
        "sample_count": 1, "variance": "insufficient - single observation",
        "performance_claim": ("single-run latency only; not median/P95 and not "
                              "the full desk decision path"),
        "config": CHAMPION_CONFIG, "config_digest": CONFIG_DIGEST,
        "child_config_digest": child.get("config_digest", ""),
        "config_match": bool(child.get("config_match", False)),
        "provider": "nvidia", "model": MODEL, "symbol": symbol, "date": date,
        "deadline_seconds": deadline, "wall_seconds": round(elapsed, 3),
        "elapsed_seconds": round(elapsed, 3),
        "graph_seconds": child.get("graph_seconds"),
        "decision": child.get("decision", "UNKNOWN"),
        "rating": child.get("rating", ""),
        "status": status, "error": error, "cleanup": cleanup,
        "counts": child.get("counts") or progress.get("counts") or {},
        "evidence_complete": evidence_complete, "reports": reports,
        "stage": progress.get("stage", ""), "progress": progress,
        "child": child, "stderr_redacted": redact(err or "")[:600],
        "shadow_only": True, "orders_submitted": 0,
        "entry_permission": "none - measurement only",
        "promotion": "none", "enforcement": "off",
        "note": ("Shadow research benchmark. No broker, no authorization, "
                 "no champion artifact write, no order submission."),
    }
    (run / "measurement.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--date", default=datetime.now(ET).date().isoformat())
    ap.add_argument("--deadline", type=float, default=1500.0)
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
                     deadline=args.deadline)
    print(f"{report['status']}: {report['wall_seconds']}s -> {args.run_id} "
          f"decision={report['decision']} evidence={report['evidence_complete']}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
