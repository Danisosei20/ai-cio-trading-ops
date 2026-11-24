#!/usr/bin/env python3
"""Bounded parallel NVIDIA NIM research CHALLENGER - shadow only.

The sequential research path in ``tradingagents_paper_auto.py`` remains the
champion and the only producer of authoritative artifacts. This challenger:
  * runs at most ``--max-workers`` bounded tasks (default 3),
  * writes ONLY under its own ``--out-root`` (never outputs/paper/tradingagents),
  * never imports an order, authorization or broker-submission module,
  * never produces a trading decision - incomplete work fails closed,
  * never promotes itself.

By default the provider callable is NOT invoked; results are marked as a
laboratory exercise. Pass a provider callable programmatically, or invoke from
the TA venv with ``--enable-nim`` to use the configured NIM graph adapter.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from robinhood_tools.research_metrics import LatencyRecorder, bounded_map, rate_limit_backoff

ET = ZoneInfo("America/New_York")
PROVIDER = "nvidia"
MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
AUTHORITATIVE_ROOT = Path("outputs/paper/tradingagents")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,10}(\.[A-Z0-9]{1,3})?$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def validate_symbol(symbol: str) -> str:
    value = str(symbol).strip().upper()
    if not SYMBOL_RE.match(value) or ".." in value:
        raise ValueError(f"invalid shadow symbol: {symbol!r}")
    return value


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.match(str(run_id)):
        raise ValueError(f"invalid run id: {run_id!r}")
    return str(run_id)


class ProviderError(Exception):
    """Base class for provider failures."""

    def __init__(self, message: str, category: str = "unknown"):
        super().__init__(message)
        self.category = category


def classify_provider_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return "rate_limit"
    if "timeout" in text or "timed out" in text or "deadline" in text:
        return "timeout"
    if "401" in text or "403" in text or "auth" in text or "key" in text:
        return "auth"
    if "parse" in text or "json" in text or "malformed" in text:
        return "malformed"
    if isinstance(exc, ProviderError):
        return exc.category
    return "unknown"


class RateLimiter:
    """Process-wide minimum-interval limiter with a bounded wait."""

    def __init__(self, *, min_interval_seconds: float = 1.0,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = max(0.0, float(min_interval_seconds))
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last = clock()

    def acquire(self, *, max_wait_seconds: float = 30.0) -> float:
        waited = 0.0
        with self._lock:
            while True:
                delay = self.min_interval - (self._clock() - self._last)
                if delay <= 0 or waited >= max_wait_seconds:
                    break
                self._sleep(min(delay, max_wait_seconds - waited))
                new_wait = max(0.0, self._clock() - self._last)
                if new_wait <= waited:  # clock not advancing - avoid spin
                    break
                waited = new_wait
            self._last = self._clock()
        return round(waited, 6)


@dataclass
class ResearchTask:
    symbol: str
    trade_date: str
    run_id: str
    prompt_version: str = "phase8.2-v1"
    tool_rounds: int = 4
    evidence_sources: list[str] = field(default_factory=list)


@dataclass
class ResearchOutcome:
    symbol: str
    status: str
    decision: str = "UNKNOWN"
    provider: str = PROVIDER
    model: str = MODEL
    attempts: int = 0
    retry_categories: list[str] = field(default_factory=list)
    queue_wait_seconds: float = 0.0
    request_seconds: float = 0.0
    backoff_seconds: float = 0.0
    evidence_complete: bool = False
    evidence_keys: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    error: str = ""
    shadow_only: bool = True
    orders_submitted: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def research_symbol(task: ResearchTask, provider_callable: Callable[[ResearchTask], dict],
                    *, recorder: LatencyRecorder | None = None,
                    limiter: RateLimiter | None = None,
                    max_retries: int = 1,
                    on_sleep: Callable[[float], None] = time.sleep,
                    cancel: threading.Event | None = None,
                    timeout_seconds: float | None = None) -> ResearchOutcome:
    """Call a provider for one symbol with bounded retries and fail-closed output."""
    outcome = ResearchOutcome(symbol=task.symbol, status="error")
    recorder = recorder or LatencyRecorder()
    symbol_started = time.monotonic()
    recorder.start(task.symbol, "total")
    recorder.start(task.symbol, "queue")
    waited = limiter.acquire() if limiter else 0.0
    outcome.queue_wait_seconds = waited
    recorder.record(task.symbol, "queue", duration_seconds=waited)

    for attempt in range(max_retries + 1):
        if cancel is not None and cancel.is_set():
            outcome.status = "cancelled"
            outcome.error = "cancelled before provider call"
            recorder.finish(task.symbol, "total", status="cancelled", reason=outcome.error)
            return outcome
        outcome.attempts = attempt + 1
        recorder.start(task.symbol, "model_request")
        try:
            payload = provider_callable(task)
            outcome.request_seconds = recorder.finish(
                task.symbol, "model_request").duration_seconds
            if not isinstance(payload, dict):
                raise ProviderError("provider returned non-dict payload", "malformed")
            missing = [str(x) for x in payload.get("missing_evidence") or []]
            outcome.missing_evidence = missing
            outcome.evidence_keys = sorted(
                str(k) for k in (payload.get("evidence_keys") or []))
            # Only a literal boolean True with no missing evidence is complete.
            complete = (payload.get("evidence_complete") is True and not missing)
            outcome.evidence_complete = complete
            raw_decision = str(payload.get("decision") or "UNKNOWN")
            outcome.decision = raw_decision if complete else "UNKNOWN"
            outcome.status = "ok" if complete else "incomplete_evidence"
            if timeout_seconds is not None and \
                    time.monotonic() - symbol_started > timeout_seconds:
                outcome.status = "timeout"
                outcome.decision = "UNKNOWN"
                outcome.error = "per-symbol deadline exceeded"
            recorder.finish(task.symbol, "total", status=outcome.status,
                            reason=outcome.error)
            return outcome
        except Exception as exc:  # noqa: BLE001 - classified, never fabricated
            category = classify_provider_error(exc)
            outcome.request_seconds = recorder.finish(
                task.symbol, "model_request", status="error", reason=category).duration_seconds
            outcome.retry_categories.append(category)
            outcome.error = f"{category}: {str(exc)[:200]}"
            if category not in {"rate_limit", "timeout"} or attempt >= max_retries:
                outcome.status = "error"
                break
            delay = rate_limit_backoff(attempt, base_seconds=0.2, max_seconds=2.0,
                                       sleep=on_sleep)
            outcome.backoff_seconds += delay
    recorder.finish(task.symbol, "total", status=outcome.status, reason=outcome.error)
    return outcome


def run_challenger(tasks: list[ResearchTask], provider_callable: Callable,
                   *, out_root: Path, run_id: str,
                   max_workers: int = 3, per_symbol_timeout: float = 60.0,
                   batch_timeout: float | None = None,
                   max_retries: int = 1,
                   rate_per_minute: float = 60.0,
                   live_nim: bool = False,
                   clock: Callable[[], float] = time.monotonic) -> dict:
    """Run bounded shadow research and persist only isolated artifacts."""
    run_id = validate_run_id(run_id)
    tasks = [ResearchTask(validate_symbol(t.symbol), t.trade_date, run_id,
                          t.prompt_version, t.tool_rounds, t.evidence_sources)
             for t in tasks]
    out_root = Path(out_root).resolve()
    parts = [str(part).lower() for part in out_root.parts]
    joined = "/".join(parts)
    if "outputs/paper/tradingagents" in joined:
        raise ValueError("challenger may not write inside the champion artifact root")
    if len(parts) < 4 or parts[-4:] != ["outputs", "shadow", "research", run_id.lower()]:
        raise ValueError("out_root must end with outputs/shadow/research/<run_id>")
    for parent in (out_root, *out_root.parents):
        if parent.parent == parent:
            break
        if parent.is_symlink():
            raise ValueError(f"unsafe symlink in output path: {parent}")

    recorder = LatencyRecorder()
    limiter = RateLimiter(min_interval_seconds=60.0 / max(1.0, rate_per_minute))
    batch_cancel = threading.Event()
    waves = max(1, math.ceil(len(tasks) / max(1, min(max_workers, 8))))
    budget = per_symbol_timeout * waves + 5.0 * waves
    deadline = clock() + min(budget, batch_timeout) if batch_timeout else clock() + budget

    def work(task, cancel=None):
        return research_symbol(task, provider_callable, recorder=recorder,
                               limiter=limiter, max_retries=max_retries,
                               cancel=cancel or batch_cancel,
                               timeout_seconds=per_symbol_timeout)

    batch_start = clock()
    batch = bounded_map(tasks, work, max_workers=max_workers,
                        per_item_timeout=budget, deadline=deadline,
                        cancel=batch_cancel)

    outcomes = []
    for index, task in enumerate(tasks):
        entry = batch.results.get(index)
        if entry is not None:
            outcome = entry["value"]
        elif index in batch.timed_out:
            outcome = ResearchOutcome(symbol=task.symbol, status="timeout",
                                      error="per-symbol deadline exceeded")
        elif index in batch.failed:
            outcome = ResearchOutcome(symbol=task.symbol, status="error",
                                      error=batch.failed[index])
        else:
            outcome = ResearchOutcome(symbol=task.symbol, status="not_evaluated",
                                      error="cancelled or skipped before dispatch")
        outcomes.append(outcome)

    # Deterministic aggregation by symbol; late workers cannot affect outputs.
    outcomes.sort(key=lambda o: o.symbol)
    run_dir = out_root
    run_dir.mkdir(parents=True, exist_ok=True)
    def safe_write(path: Path, text: str) -> None:
        if path.is_symlink() or not path.resolve().is_relative_to(run_dir.resolve()):
            raise ValueError(f"unsafe challenger output path: {path}")
        path.write_text(text, encoding="utf-8")

    for outcome in outcomes:
        folder = run_dir / outcome.symbol
        folder.mkdir(parents=True, exist_ok=True)
        if folder.is_symlink() or not folder.resolve().is_relative_to(run_dir.resolve()):
            raise ValueError(f"unsafe challenger folder: {folder}")
        safe_write(folder / "challenger.json",
                   json.dumps({**outcome.as_dict(), "shadow_only": True,
                               "orders_submitted": 0, "entry_permission": "none",
                               "enforcement_enabled": False}, indent=2) + "\n")

    durations = [o.queue_wait_seconds + o.request_seconds for o in outcomes]
    durations.sort()
    report = {
        "track": "parallel_nim_research_challenger",
        "generated_at": datetime.now(ET).isoformat(),
        "run_id": run_id,
        "mode": "nim" if live_nim else "laboratory",
        "provider": PROVIDER,
        "model": MODEL,
        "max_workers": max_workers,
        "rate_per_minute": rate_per_minute,
        "batch_wall_seconds": round(clock() - batch_start, 6),
        "completed": sum(o.status == "ok" for o in outcomes),
        "timeouts": sum(o.status == "timeout" for o in outcomes),
        "errors": sum(o.status == "error" for o in outcomes),
        "not_evaluated": sum(o.status == "not_evaluated" for o in outcomes),
        "latency": {"by_phase": recorder.summary().get("by_phase", {}),
                    "note": "phases are reported separately; no pooled percentile"},
        "outcomes": [o.as_dict() for o in outcomes],
        "shadow_only": True,
        "orders_submitted": 0,
        "entry_permission": "none - challenger research only; champion routing unchanged",
        "enforcement_enabled": False,
        "promotion_recommendation": "none - requires multi-session evaluation and owner approval",
        "note": "No result here is an order, fill, or realized profit.",
    }
    safe_write(run_dir / "batch.json", json.dumps(report, indent=2) + "\n")
    return report


def laboratory_provider(task: ResearchTask) -> dict:
    """Explicitly simulated provider; outcomes are clearly marked as such."""
    def stub(_task):
        return {"decision": "UNKNOWN", "evidence_complete": False,
                "missing_evidence": ["laboratory provider supplies no research"]}
    stub.laboratory = True
    return stub(task)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out-root", default=None)
    ap.add_argument("--max-workers", type=int, default=3)
    ap.add_argument("--per-symbol-timeout", type=float, default=60.0)
    ap.add_argument("--batch-timeout", type=float, default=None)
    ap.add_argument("--rate-per-minute", type=float, default=60.0)
    ap.add_argument("--max-retries", type=int, default=1)
    ap.add_argument("--enable-nim", action="store_true",
                    help="use a real NIM provider adapter (must be supplied by caller)")
    args = ap.parse_args(argv)
    out_root = Path(args.out_root) if args.out_root else (
        Path("outputs/shadow/research") / args.run_id)
    tasks = [ResearchTask(symbol=s.strip().upper(), trade_date=args.date,
                          run_id=args.run_id)
             for s in args.symbols.split(",") if s.strip()]
    provider = laboratory_provider
    if args.enable_nim:
        print("--enable-nim requires a real provider callable; CLI mode stays laboratory",
              file=__import__("sys").stderr)
        return 2
    report = run_challenger(tasks, provider, out_root=out_root, run_id=args.run_id,
                            max_workers=args.max_workers,
                            per_symbol_timeout=args.per_symbol_timeout,
                            batch_timeout=args.batch_timeout,
                            max_retries=args.max_retries,
                            rate_per_minute=args.rate_per_minute)
    print(f"shadow challenger: completed={report['completed']} "
          f"timeouts={report['timeouts']} orders=0 mode={report['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
