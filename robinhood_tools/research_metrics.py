#!/usr/bin/env python3
"""Latency instrumentation and bounded, fail-closed research helpers.

Timing uses ``time.monotonic``; persisted events use UTC ISO strings. The
helpers here are engine-agnostic (no broker, no LLM imports) so they can be
tested deterministically and adopted behind a champion/challenger flag.
"""
from __future__ import annotations

import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PhaseTiming:
    phase: str
    started_at: str
    ended_at: str = ""
    duration_seconds: float = 0.0
    status: str = "pending"
    reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class LatencyRecorder:
    """Record per-symbol, per-phase monotonic durations plus UTC stamps."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], str] = utc_now):
        self._clock = clock
        self._now = now
        self._starts: dict[tuple[str, str], tuple[float, str]] = {}
        self.timings: list[PhaseTiming] = []
        self._lock = threading.Lock()

    def start(self, symbol: str, phase: str) -> None:
        with self._lock:
            self._starts[(symbol, phase)] = (self._clock(), self._now())

    def finish(self, symbol: str, phase: str, *, status: str = "ok",
               reason: str = "") -> PhaseTiming:
        with self._lock:
            started, stamp = self._starts.pop((symbol, phase), (None, self._now()))
            if started is None:
                item = PhaseTiming(phase=phase, started_at=stamp, ended_at=stamp,
                                   duration_seconds=0.0, status="unstarted",
                                   reason="finish() called without start()")
            else:
                item = PhaseTiming(
                    phase=phase, started_at=stamp, ended_at=self._now(),
                    duration_seconds=round(max(0.0, self._clock() - started), 6),
                    status=status, reason=reason[:300])
            self.timings.append(item)
        return item

    def record(self, symbol: str, phase: str, *, duration_seconds: float,
               status: str = "ok", reason: str = "") -> PhaseTiming:
        item = PhaseTiming(phase=phase, started_at=self._now(), ended_at=self._now(),
                           duration_seconds=round(float(duration_seconds), 6),
                           status=status, reason=reason[:300])
        with self._lock:
            self.timings.append(item)
        return item

    def summary(self) -> dict:
        with self._lock:
            values = sorted(t.duration_seconds for t in self.timings)
        if not values:
            return {"count": 0, "median_seconds": None, "p95_seconds": None,
                    "total_seconds": 0.0}
        median = values[len(values) // 2]
        p95 = values[min(len(values) - 1, int(len(values) * 0.95))]
        return {"count": len(values), "median_seconds": round(median, 6),
                "p95_seconds": round(p95, 6),
                "total_seconds": round(sum(values), 6),
                "by_phase": self._by_phase_locked()}

    def _by_phase_locked(self) -> dict:
        out: dict[str, dict] = {}
        with self._lock:
            timings = list(self.timings)
        for t in timings:
            slot = out.setdefault(t.phase, {"count": 0, "total_seconds": 0.0})
            slot["count"] += 1
            slot["total_seconds"] = round(slot["total_seconds"] + t.duration_seconds, 6)
        return out

    def as_list(self) -> list[dict]:
        with self._lock:
            return [t.as_dict() for t in self.timings]


@dataclass
class BoundedResult:
    results: dict[int, Any] = field(default_factory=dict)
    timed_out: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)
    skipped: list[int] = field(default_factory=list)
    deadline_exceeded: bool = False

    def as_dict(self) -> dict:
        return {"results": self.results, "timed_out": self.timed_out,
                "failed": self.failed, "skipped": self.skipped,
                "deadline_exceeded": self.deadline_exceeded}


def bounded_map(items: list, fn: Callable[[Any], Any], *, max_workers: int = 3,
                per_item_timeout: float = 30.0, deadline: float | None = None,
                clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep,
                cancel: threading.Event | None = None) -> BoundedResult:
    """Run a bounded number of daemon workers with explicit deadlines.

    Never unbounded: at most ``max_workers`` threads are created. Items that
    do not finish inside the budget are recorded as ``timed_out`` and are NOT
    retried blindly. Cooperative cancellation is exposed to ``fn`` through a
    second positional argument when it accepts one.
    """
    items = list(items)
    results: dict[int, Any] = {}
    failed: dict[int, str] = {}
    timed_out: list[int] = []
    local_cancel = threading.Event()
    try:
        accepts_cancel = "cancel" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        accepts_cancel = False

    def run(item):
        started = clock()
        value = fn(item, local_cancel) if accepts_cancel else fn(item)
        return value, round(max(0.0, clock() - started), 6)

    # Bounded pool, capped globally by call size. shutdown(wait=False) plus
    # cancel_futures prevents join-on-exit stalls while keeping worker count
    # limited. Results are snapshotted before return so late workers can never
    # mutate the caller's object.
    limit = max(1, min(int(max_workers), 8, max(1, len(items))))
    executor = ThreadPoolExecutor(max_workers=limit)
    futures = {}
    try:
        for idx, item in enumerate(items):
            if cancel is not None and cancel.is_set():
                break
            futures[executor.submit(run, item)] = idx
        budget = max(0.0, float(per_item_timeout))
        if deadline is not None:
            budget = min(budget, max(0.0, deadline - clock()))
        done, pending = wait(futures, timeout=budget)
        for future in done:
            idx = futures[future]
            try:
                value, duration = future.result()
                results[idx] = {"value": value, "duration_seconds": duration}
            except Exception as exc:  # noqa: BLE001 - reported, never silently dropped
                failed[idx] = f"{type(exc).__name__}: {exc}"
        for future in pending:
            future.cancel()
            timed_out.append(futures[future])
    finally:
        local_cancel.set()
        executor.shutdown(wait=False, cancel_futures=True)

    accounted = set(results) | set(failed) | set(timed_out)
    skipped = [i for i in range(len(items)) if i not in accounted]
    exceeded = bool(timed_out or skipped or
                    (deadline is not None and clock() >= deadline))
    return BoundedResult(results=dict(results), timed_out=sorted(timed_out),
                         failed=dict(failed), skipped=skipped,
                         deadline_exceeded=exceeded)


@dataclass(frozen=True)
class Evidence:
    source: str
    instrument: str
    retrieved_at: str
    as_of: str
    expires_at: str
    data_version: str
    value: Any

    def is_fresh(self, *, now: str | None = None,
                 now_fn: Callable[[], str] = utc_now) -> bool:
        """Compare normalized UTC instants, never raw ISO strings."""
        current = now or now_fn()
        if not self.as_of or not self.expires_at:
            return False
        try:
            def to_utc(value):
                from datetime import datetime  # noqa: PLC0415
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return (parsed if parsed.tzinfo else
                        parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            return to_utc(self.as_of) <= to_utc(current) <= to_utc(self.expires_at)
        except (ValueError, TypeError):
            return False


class EvidenceCache:
    """TTL cache that refuses to reuse stale market evidence for execution."""

    def __init__(self, *, now_fn: Callable[[], str] = utc_now):
        self._now = now_fn
        self._store: dict[str, Evidence] = {}

    def put(self, key: str, evidence: Evidence) -> None:
        self._store[key] = evidence

    def get(self, key: str) -> Evidence | None:
        item = self._store.get(key)
        if item is None:
            return None
        if not item.is_fresh(now_fn=self._now):
            self._store.pop(key, None)
            return None
        return item


@dataclass(frozen=True)
class PrefilterResult:
    eligible: bool
    reason: str
    gate: str


def rate_limit_backoff(attempt: int, *, base_seconds: float = 0.5,
                       max_seconds: float = 8.0,
                       sleep: Callable[[float], None] = time.sleep) -> float:
    """Bounded exponential backoff for provider rate limits (never unbounded)."""
    delay = min(max_seconds, base_seconds * (2 ** max(0, int(attempt))))
    sleep(delay)
    return delay


def deterministic_prefilter(*, symbol: str, bars: int, min_bars: int,
                            quote_age_seconds: float | None, max_quote_age_seconds: float,
                            spread_pct: float | None, max_spread_pct: float,
                            new_risk: bool, regime: str,
                            session_open: bool, cutoff_passed: bool) -> PrefilterResult:
    """Cheap gates BEFORE spending AI inference. No gate is weakened here."""
    if not session_open or cutoff_passed:
        return PrefilterResult(False, "session closed or entry cutoff passed", "session")
    if bars < min_bars:
        return PrefilterResult(False, f"insufficient bars {bars} < {min_bars}", "history")
    if quote_age_seconds is None:
        return PrefilterResult(False, "quote age unavailable", "quote")
    if quote_age_seconds > max_quote_age_seconds:
        return PrefilterResult(False, f"stale quote {quote_age_seconds}s", "quote")
    if spread_pct is None:
        return PrefilterResult(False, "spread unavailable", "liquidity")
    if spread_pct > max_spread_pct:
        return PrefilterResult(False, f"spread {spread_pct}% too wide", "liquidity")
    if new_risk and regime != "RISK_ON":
        return PrefilterResult(False, f"new risk blocked: {regime}", "regime")
    return PrefilterResult(True, "eligible for research", "pass")
