#!/usr/bin/env python3
"""Real NVIDIA NIM latency benchmark for the shadow challenger.

Uses the configured provider/model and actual HTTP requests when a key is
available. This measures PROVIDER latency only - it does not run the full
TradingAgents research graph and produces no trading decisions or authorization.
If the API is unavailable, the result is explicitly LABORATORY ONLY.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

ROOT_MODULE = Path(__file__).resolve().parents[1]
if str(ROOT_MODULE) not in sys.path:
    sys.path.insert(0, str(ROOT_MODULE))

from scripts import champion_challenger as cc  # noqa: E402
from scripts import shadow_research_challenger as src  # noqa: E402

ET = ZoneInfo("America/New_York")
DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
PROMPT_VERSION = "phase8.3-provider-latency-v1"


def load_nim_key() -> str:
    if os.environ.get("NVIDIA_API_KEY"):
        return os.environ["NVIDIA_API_KEY"]
    candidates = [Path(__file__).resolve().parents[2] / "TradingAgents" / ".env",
                  Path(r"C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.env")]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            key_name = "NVIDIA_" + "API_KEY"
            if line.startswith(key_name + "="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return ""


def nim_provider(model: str = DEFAULT_MODEL, *, max_tokens: int = 8,
                 timeout: float = 30.0) -> Callable:
    """Build a provider callable that performs a real NIM request."""
    key = load_nim_key()

    def provider(task):
        if not key:
            raise PermissionError("NVIDIA_API_KEY unavailable")
        body = {
            "model": model,
            "messages": [{"role": "user",
                          "content": f"Benchmark token for {task.symbol}. Reply with OK."}],
            "max_tokens": max_tokens, "temperature": 0,
        }
        request = urllib.request.Request(
            NIM_URL, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "User-Agent": "phase8.3-shadow-benchmark"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read() or b"{}")
        if not isinstance(payload, dict) or not payload.get("choices"):
            raise ValueError("NIM response missing choices")
        # Provider latency only: no research conclusion is inferred.
        return {"decision": "UNKNOWN", "evidence_complete": False,
                "missing_evidence": ["provider-latency benchmark; no research graph"],
                "provider_model": payload.get("model", model)}

    provider.laboratory = False
    provider.model = model
    return provider


def run_benchmark(symbols: list[str], *, workers: int = 3,
                  out_root: Path | None = None, run_id: str = "nim-live") -> dict:
    model = DEFAULT_MODEL
    provider = nim_provider(model)
    tasks = [src.ResearchTask(s, datetime.now(ET).date().isoformat(), run_id,
                              PROMPT_VERSION, 0) for s in symbols]

    t0 = time.perf_counter()
    sequential = []
    for task in tasks:
        started = time.perf_counter()
        try:
            outcome = src.research_symbol(task, provider, timeout_seconds=30.0)
        except Exception as exc:  # noqa: BLE001 - classification, never fabricated
            outcome = src.ResearchOutcome(symbol=task.symbol, status="error",
                                          error=f"{type(exc).__name__}: {exc}")
        outcome.request_seconds = round(time.perf_counter() - started, 3)
        sequential.append(outcome)
    sequential_wall = time.perf_counter() - t0

    root = out_root or Path("outputs/shadow/research") / run_id
    t0 = time.perf_counter()
    batch = src.run_challenger(tasks, provider, out_root=root, run_id=run_id,
                               max_workers=workers, per_symbol_timeout=30.0,
                               rate_per_minute=600.0, live_nim=True)
    parallel_wall = time.perf_counter() - t0

    succeeded = [o for o in sequential if o.status in {"ok", "incomplete_evidence"}]
    mode = "live_nim" if succeeded else "laboratory_only_unavailable"
    champion = {"mode": "sequential_live_nim", "sessions": 1,
                "outcomes": [{**o.as_dict()} for o in sequential]}
    comparison = cc.compare(champion, batch)
    return {
        "track": "nim_provider_latency_benchmark",
        "generated_at": datetime.now(ET).isoformat(),
        "mode": mode,
        "provider": "nvidia", "model": model, "prompt_version": PROMPT_VERSION,
        "tool_rounds": 0,
        "symbols": symbols, "workers": workers,
        "sequential_wall_seconds": round(sequential_wall, 3),
        "parallel_wall_seconds": round(parallel_wall, 3),
        "speedup": round(sequential_wall / parallel_wall, 3) if parallel_wall else None,
        "provider_latency_sequential_mean": round(
            sum(o.request_seconds for o in sequential) / max(1, len(sequential)), 3),
        "provider_latency_parallel_mean": round(
            sum(float(o.get("request_seconds") or 0) for o in batch["outcomes"]) /
            max(1, len(batch["outcomes"])), 3),
        "comparison_scope": ("wall clock includes client queueing/rate limiting; "
                             "provider latency means are like-for-like"),
        "sequential_succeeded": len(succeeded),
        "sequential_outcomes": [o.as_dict() for o in sequential],
        "parallel_outcomes": batch["outcomes"],
        "parallel_responses": sum(1 for o in batch["outcomes"]
                                  if o.get("status") in {"ok", "incomplete_evidence"}),
        "parallel_completed": batch["completed"],
        "parallel_timeouts": batch["timeouts"],
        "comparison": comparison,
        "shadow_only": True, "orders_submitted": 0,
        "entry_permission": "none - provider latency benchmark only",
        "enforcement_enabled": False,
        "note": ("Real NIM provider requests; NOT the full research graph, NOT a "
                 "trading decision, NOT a promotion signal. Late results cannot affect decisions."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="QQQ,SPY,NVDA,AAPL,AMD,MSFT")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--run-id", default="nim-live")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    report = run_benchmark(symbols, workers=args.workers, run_id=args.run_id)
    out = args.out or Path("outputs/reports/research") / \
        f"{datetime.now(ET).date().isoformat()}-nim-live-benchmark.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{report['mode']}: {len(symbols)} symbols, speedup {report['speedup']}, "
          f"orders 0 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
