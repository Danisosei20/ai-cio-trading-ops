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


# The twelve registered stages, in execution order. ``<run_id>`` and
# ``<symbol>`` are left as literal placeholders for the runner to substitute.
STAGES: tuple[StageSpec, ...] = (
    StageSpec(
        agent="Reconciler",
        role="reconcile fills",
        argv=("scripts/reconcile_fills.py", "--quiet"),
        primary_artifact="outputs/paper/fill_reconciliation.json",
        required=True,
        allow_stale=False,
    ),
    StageSpec(
        agent="ExitGuard",
        role="guard open positions",
        argv=("scripts/options_guard.py", "--execute"),
        primary_artifact="outputs/paper/options_guard.json",
        required=True,
        allow_stale=False,
    ),
    StageSpec(
        agent="OddsAnalyst",
        role="score position odds",
        argv=("scripts/options_time_machine.py", "--book", "--allow-stale"),
        primary_artifact="outputs/simulation/options_time_machine.json",
        required=False,
        allow_stale=True,
    ),
    StageSpec(
        agent="ScenarioAnalyst",
        role="stress the book across scenarios",
        argv=("scripts/scenario_analysis.py",),
        primary_artifact="outputs/simulation/scenario_analysis.json",
        required=False,
        allow_stale=False,
    ),
    StageSpec(
        agent="PerformanceAnalyst",
        role="summarize realized performance by scenario",
        argv=("scripts/performance_by_scenario.py",),
        primary_artifact="outputs/simulation/performance_by_scenario.json",
        required=False,
        allow_stale=False,
    ),
    StageSpec(
        agent="Certifier",
        role="certify execution paths",
        argv=("scripts/paper_certification.py",),
        primary_artifact="outputs/certification/paper_certification.json",
        required=True,
        allow_stale=False,
    ),
    StageSpec(
        agent="Screener",
        role="screen the trading universe",
        argv=("scripts/universe_screen.py", "--top", "10", "--out",
              "outputs/screener/latest.json"),
        primary_artifact="outputs/screener/latest.json",
        required=True,
        allow_stale=False,
    ),
    StageSpec(
        agent="ZoneAnalyst",
        role="read market zones",
        argv=("scripts/zone_analysis.py", "--tickers", "SPY,QQQ,NVDA,AMD,AAPL"),
        primary_artifact="outputs/orchestrator/<run_id>/zone.json",
        required=True,
        allow_stale=False,
    ),
    StageSpec(
        agent="ResearchFleet",
        role="research the screened names (dry run)",
        argv=(
            "scripts/tradingagents_paper_auto.py",
            "--screen", "outputs/screener/latest.json",
            "--screen-top", "7",
            "--ticker-timeout", "1200",
            "--options",
            "--premium-cap", "3000",
            "--max-option-positions", "5",
            "--dry-run",
        ),
        primary_artifact="outputs/orchestrator/<run_id>/research.json",
        required=True,
        allow_stale=False,
    ),
    StageSpec(
        agent="BarReader",
        role="read bars for a symbol",
        argv=("scripts/desk_bar_reader.py", "--out",
              "outputs/orchestrator/<run_id>/bars_<symbol>.json"),
        primary_artifact="outputs/orchestrator/<run_id>/bars_<symbol>.json",
        required=True,
        allow_stale=False,
    ),
    StageSpec(
        agent="Reporter",
        role="assemble the desk report",
        argv=("scripts/run_summary.py",),
        primary_artifact="outputs/paper/slack_thread.json",
        required=False,
        allow_stale=False,
    ),
    StageSpec(
        agent="OrderAgent",
        role="place paper orders",
        argv=(
            "scripts/tradingagents_paper_auto.py",
            "--screen", "outputs/screener/latest.json",
            "--screen-top", "7",
            "--options",
            "--premium-cap", "3000",
            "--max-option-positions", "1",
        ),
        primary_artifact="outputs/paper/tradingagents/**/auto.json",
        required=False,
        allow_stale=False,
    ),
)


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
