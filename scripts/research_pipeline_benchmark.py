#!/usr/bin/env python3
"""Measured research-pipeline phases: sequential champion vs shadow challenger.

Only the NIM inference phase uses a real provider request. Local phases
(screening, evidence validation, decision preparation) are measured real
computations on the same input snapshot, not the full TradingAgents graph.
The result is labelled `live_nim_partial_pipeline`, never a full-graph result.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT_MODULE = Path(__file__).resolve().parents[1]
if str(ROOT_MODULE) not in sys.path:
    sys.path.insert(0, str(ROOT_MODULE))

from robinhood_tools import bar_validation  # noqa: E402
from robinhood_tools.research_metrics import LatencyRecorder  # noqa: E402
from scripts import nim_live_benchmark as nib  # noqa: E402

ET = ZoneInfo("America/New_York")
PHASES = ("technical_screening", "research_planning", "nim_inference",
          "tool_calls", "news_retrieval", "evidence_validation",
          "decision_preparation")


def make_bars(*, now=None):
    """Bars relative to now, ending at the last completed 5-minute boundary."""
    import datetime as dt  # noqa: PLC0415
    current = now or datetime.now(dt.timezone.utc)
    last = current.replace(second=0, microsecond=0)
    last -= dt.timedelta(minutes=last.minute % 5 + 5)
    start = last - dt.timedelta(minutes=5 * 19)
    return [{"t": (start + dt.timedelta(minutes=5 * i)).isoformat(),
             "h": 101 + i * 0.1, "l": 100 + i * 0.1,
             "c": 100.5 + i * 0.1, "v": 1000 + i} for i in range(20)]


def pipeline(symbol: str, provider, recorder: LatencyRecorder,
             task_id: str) -> dict:
    key = f"{task_id}:{symbol}"
    bars = make_bars()
    out = {"symbol": symbol}
    recorder.start(key, "technical_screening")
    validated = bar_validation.validate_bars(
        bars, feed="iex", now=datetime.now(__import__("datetime").timezone.utc))
    recorder.finish(key, "technical_screening", status=validated.status,
                    reason=validated.reason)
    out["screen_status"] = validated.status

    for phase in ("research_planning",):
        recorder.start(key, phase)
        prompt = {"symbol": symbol, "version": "phase8.4-pipeline-v1"}
        recorder.finish(key, phase)
        out["prompt_version"] = prompt["version"]

    recorder.start(key, "nim_inference")
    try:
        provider(type("T", (), {"symbol": symbol})())
        status = "ok"
    except Exception as exc:  # noqa: BLE001 - never fabricate success
        status = f"error:{type(exc).__name__}"
    recorder.finish(key, "nim_inference", status=status)
    out["nim_status"] = status

    for phase in ("tool_calls", "news_retrieval"):
        recorder.start(key, phase)
        recorder.finish(key, phase, status="not_run_in_partial_pipeline")

    recorder.start(key, "evidence_validation")
    evidence_ok = validated.status == "ok" and status == "ok"
    recorder.finish(key, "evidence_validation",
                    status="ok" if evidence_ok else "rejected")
    out["evidence_complete"] = evidence_ok

    recorder.start(key, "decision_preparation")
    out["decision"] = "UNKNOWN"  # no trading decision is inferred here
    recorder.finish(key, "decision_preparation")
    return out


def run(symbols: list[str], *, workers: int = 3) -> dict:
    provider = nib.nim_provider()
    champion_rec = LatencyRecorder()
    t0 = time.perf_counter()
    champion = [pipeline(s, provider, champion_rec, f"champion-{i}")
               for i, s in enumerate(symbols)]
    champion_wall = time.perf_counter() - t0

    challenger_rec = LatencyRecorder()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        futures = [pool.submit(pipeline, s, provider, challenger_rec,
                               f"challenger-{i}")
                   for i, s in enumerate(symbols)]
        challenger = [f.result() for f in futures]
    challenger_wall = time.perf_counter() - t0

    def summary(rec):
        data = rec.summary()
        return {"count": data["count"], "median_seconds": data["median_seconds"],
                "p95_seconds": data["p95_seconds"],
                "total_seconds": data["total_seconds"],
                "by_phase": data["by_phase"]}

    return {
        "track": "research_pipeline_latency",
        "generated_at": datetime.now(ET).isoformat(),
        "mode": ("live_nim_partial_pipeline" if all(
            r["nim_status"] == "ok" for r in champion + challenger)
            else "laboratory_only_unavailable"),
        "scope": ("real NIM inference + measured local screening/validation; "
                  "NOT the full TradingAgents research graph"),
        "model": nib.DEFAULT_MODEL,
        "phases": PHASES,
        "symbols": symbols, "workers": workers,
        "champion": {"wall_seconds": round(champion_wall, 3),
                     "latency": summary(champion_rec),
                     "completed": sum(r["evidence_complete"] for r in champion)},
        "challenger": {"wall_seconds": round(challenger_wall, 3),
                       "latency": summary(challenger_rec),
                       "completed": sum(r["evidence_complete"] for r in challenger)},
        "speedup": round(champion_wall / challenger_wall, 3) if challenger_wall else None,
        "shadow_only": True, "orders_submitted": 0,
        "entry_permission": "none - latency measurement only",
        "enforcement_enabled": False,
        "note": "No promotion, no order, no research conclusion from this benchmark.",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="QQQ,SPY,NVDA,AAPL,AMD,MSFT")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    report = run(symbols, workers=args.workers)
    out = args.out or Path("outputs/reports/research") / \
        f"{datetime.now(ET).date().isoformat()}-research-pipeline-benchmark.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{report['mode']}: {symbols!r} speedup {report['speedup']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
