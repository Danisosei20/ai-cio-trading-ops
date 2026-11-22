"""Stage descriptors and report normalization for the paper-desk orchestrator.

A stage runs a script, reads its JSON artifact, and reports one small,
fail-closed record. ``normalize`` never trusts an artifact over a nonzero
exit, and ``write_report`` persists the record under the run's directory.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class StageSpec:
    agent: str
    role: str
    argv: tuple[str, ...]
    primary_artifact: str
    required: bool
    allow_stale: bool


@dataclass(frozen=True)
class StageReport:
    agent: str
    role: str
    status: str
    summary: str
    evidence: list[str]
    metrics: dict
    started_at: str
    finished_at: str
    duration_seconds: float
    exit_code: int | None
    stderr_tail: str

    def as_dict(self) -> dict:
        return asdict(self)


def normalize(spec: StageSpec, *, proc, artifacts: dict[str, dict | None],
              now_iso: str) -> StageReport:
    """Fold one stage's process result and artifact into a StageReport."""
    doc = artifacts.get(spec.primary_artifact)
    has_artifact = isinstance(doc, dict)
    exit_code = getattr(proc, "returncode", None)
    ok = exit_code == 0 and has_artifact and bool(doc)

    metrics: dict = {}
    if has_artifact:
        for key in ("verdict", "status", "counts"):
            if key in doc:
                metrics[key] = doc[key]
        if "orphan_orders" in doc:
            value = doc["orphan_orders"]
            if isinstance(value, (list, dict)):
                metrics["orphan_orders"] = len(value)
            elif isinstance(value, int) and not isinstance(value, bool):
                metrics["orphan_orders"] = value
            else:
                metrics["orphan_orders"] = 0

    detail = "no artifact"
    if has_artifact:
        for key in ("verdict", "status"):
            value = str(doc.get(key) or "").strip()
            if value:
                detail = value
                break
        else:
            detail = "artifact present" if ok else "artifact empty"
    if exit_code not in (0, None):
        detail = f"{detail} (exit {exit_code})"
    summary = f"{spec.role}: {detail}" if spec.role else detail

    # Optional proc timing is forwarded when the runner supplies it;
    # otherwise the single known timestamp is used for both ends.
    started_at = str(getattr(proc, "started_at", None) or now_iso)
    elapsed = getattr(proc, "duration_seconds", None)
    try:
        duration_seconds = float(elapsed) if elapsed is not None else 0.0
    except (TypeError, ValueError):
        duration_seconds = 0.0

    return StageReport(
        agent=spec.agent,
        role=spec.role,
        status="ok" if ok else "fail",
        summary=summary,
        evidence=[spec.primary_artifact] if has_artifact else [],
        metrics=metrics,
        started_at=started_at,
        finished_at=now_iso,
        duration_seconds=duration_seconds,
        exit_code=exit_code,
        stderr_tail=str(getattr(proc, "stderr", "") or "")[-200:],
    )


def write_report(root: Path, run_id: str, report: StageReport) -> Path:
    """Persist one stage report under ``outputs/orchestrator/<run_id>/``."""
    path = Path(root) / "outputs" / "orchestrator" / run_id / f"{report.agent}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    return path
