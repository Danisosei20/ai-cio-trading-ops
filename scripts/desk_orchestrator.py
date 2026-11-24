"""Decision engine for the paper-desk orchestrator.

The decision is the single go/no-go gate for a run. It is deliberately
fail-closed: only a fully paper-only safety snapshot, a tradeable session, and
``ok`` reports from every required stage can produce ``proceed``. Overrides can
downgrade a ``review`` to ``proceed`` but can never clear a ``halt``.

Storage (``write_run`` / ``read_latest`` / ``prove`` / ``record_override`` /
``load_overrides``) lives here, as does the runner (``run_slot``) that executes a
slot and records one run. Audited overrides from prior runs are consumed at
decision time so an accepted ``review`` can become ``proceed``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.desk_safety_status import read_env  # noqa: E402
from scripts.desk_stage_agents import STAGES, StageSpec, StageReport, normalize  # noqa: E402

_ET = ZoneInfo("America/New_York")
_REQUIRED_AGENTS = tuple(spec.agent for spec in STAGES if spec.required)
_DEFAULT_SYMBOL = "NVDA"
_SLOTS = ("0945", "1020")
_UNAVAILABLE_SESSION = {"tradeable": False, "market_clock": "unknown", "window_open": False}


@dataclass
class RunDecision:
    """The verdict for one run, with the evidence that produced it."""

    run_id: str
    generated_at: str
    verdict: str
    reports: list[StageReport]
    reasons: list[str]
    safety: dict
    overrides: list[dict]

    def as_dict(self) -> dict:
        return asdict(self)


def _truthy_flag(value: object) -> bool:
    """Treat only a literal (case/space-insensitive) ``true`` as true."""
    return str(value or "").strip().lower() == "true"


def _et_now(now_iso: str) -> datetime:
    """Parse an ISO timestamp and express it in US Eastern time."""
    return datetime.fromisoformat(now_iso).astimezone(_ET)


def decide(
    reports: list[StageReport],
    *,
    safety: dict,
    session: dict,
    now_iso: str,
    overrides: list[dict] | None = None,
) -> RunDecision:
    """Fold stage reports, safety flags and the session into one verdict.

    Checks run in fail-closed order: safety flags, then session tradeability,
    then every required stage report. A ``halt`` reason dominates any ``warn``,
    and no override is allowed to touch a ``halt``.
    """
    et_now = _et_now(now_iso)
    halt_reasons: list[str] = []
    warn_reasons: list[str] = []
    warn_agents: list[str] = []

    # (1) Safety flags: live trading, non-paper mode, or a missing/odd flag.
    if _truthy_flag(safety.get("TRADING_ENABLED")):
        halt_reasons.append("TRADING_ENABLED=true")
    if not _truthy_flag(safety.get("PAPER_TRADING_ENABLED")):
        halt_reasons.append("PAPER_TRADING_ENABLED!=true")
    if str(safety.get("TRADING_MODE") or "").strip().lower() != "paper_auto":
        halt_reasons.append("TRADING_MODE!=paper_auto")

    # (2) Session must be explicitly tradeable; truthy substitutes do not count.
    if session.get("tradeable") is not True:
        halt_reasons.append("session.tradeable is not True")

    # (3) Required stages: missing/fail (or unknown status) halt; warn reviews.
    # Malformed input with two reports for one required agent is a halt even if
    # the later one looks healthy -- e.g. a `fail` followed by an `ok` must not
    # be collapsed into a `proceed` by the last-wins lookup below.
    required = set(_REQUIRED_AGENTS)
    counts: dict[str, int] = {}
    for report in reports:
        if report.agent in required:
            counts[report.agent] = counts.get(report.agent, 0) + 1
    for agent in _REQUIRED_AGENTS:
        if counts.get(agent, 0) > 1:
            halt_reasons.append(f"{agent}: duplicate required report")

    by_agent = {report.agent: report for report in reports}
    for agent in _REQUIRED_AGENTS:
        report = by_agent.get(agent)
        if report is None:
            halt_reasons.append(f"{agent}: missing required report")
        elif report.status == "ok":
            continue
        elif report.status == "warn":
            warn_agents.append(agent)
            warn_reasons.append(f"{agent}: warn")
        else:
            halt_reasons.append(f"{agent}: non-ok status {report.status!r}")

    reasons = halt_reasons + warn_reasons
    if halt_reasons:
        verdict = "halt"
    elif warn_agents:
        verdict = "review"
    else:
        verdict = "proceed"

    # Overrides may only downgrade review -> proceed, never clear a halt. Each
    # consumed override must name a required warn agent, match today's ET date,
    # and carry a reason. All warns must be covered before proceeding so a
    # second unaddressed warn can never be overridden into a silent proceed.
    consumed: list[dict] = []
    if verdict == "review" and overrides:
        et_date = et_now.date().isoformat()
        accepted: list[dict] = []
        overridden: set[str] = set()
        for override in overrides:
            if not isinstance(override, dict):
                continue
            agent = str(override.get("agent") or "").strip()
            reason = str(override.get("reason") or "").strip()
            date = str(override.get("date") or "").strip()
            if agent not in warn_agents or not reason or date != et_date:
                continue
            overridden.add(agent)
            accepted.append(dict(override))
        if len(overridden) == len(warn_agents):
            verdict = "proceed"
            consumed = accepted

    return RunDecision(
        run_id=et_now.strftime("%Y%m%d-%H%M"),
        generated_at=now_iso,
        verdict=verdict,
        reports=list(reports),
        reasons=reasons,
        safety=dict(safety),
        overrides=consumed,
    )


_ORCHESTRATOR_DIR = Path("outputs") / "orchestrator"


def _run_dir(root: Path, run_id: str) -> Path:
    return Path(root) / _ORCHESTRATOR_DIR / run_id


def _load_decision(run_dir: Path) -> dict | None:
    """Read a recorded decision, or ``None`` when absent or malformed."""
    try:
        doc = json.loads((run_dir / "decision.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def write_run(root: Path, run_id: str, reports: list[StageReport], decision: RunDecision) -> Path:
    """Persist one immutable run and advance the ``latest.json`` pointer.

    The run's ``decision.json`` is created exclusively, so a second write to the
    same ``run_id`` fails closed with ``FileExistsError``. The ``latest.json``
    pointer is the only file that is replaced on each run.
    """
    run_dir = _run_dir(root, run_id)
    decision_path = run_dir / "decision.json"
    if decision_path.exists():
        raise FileExistsError(str(decision_path))

    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    for report in reports:
        (reports_dir / f"{report.agent}.json").write_text(
            json.dumps(report.as_dict(), indent=2), encoding="utf-8")

    with open(decision_path, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(decision.as_dict(), indent=2))

    latest = {"run_id": run_id, "verdict": decision.verdict, "generated_at": decision.generated_at}
    (Path(root) / _ORCHESTRATOR_DIR / "latest.json").write_text(
        json.dumps(latest, indent=2), encoding="utf-8")
    return decision_path


def read_latest(root: Path) -> dict | None:
    """Return the ``latest.json`` pointer, or ``None`` if no run is recorded."""
    try:
        doc = json.loads((Path(root) / _ORCHESTRATOR_DIR / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def prove(root: Path, run_id: str) -> str:
    """Return one proof-matrix line per registered stage, in ``STAGES`` order.

    Each line is ``agent | status | evidence | decision contribution``. The
    contribution names the stage tier (``required``/``advisory``) plus any
    decision reason that names this agent.
    """
    decision = _load_decision(_run_dir(root, run_id))
    if decision is None:
        return ""
    by_agent = {r.get("agent"): r for r in decision.get("reports", []) if isinstance(r, dict)}
    reasons = [str(r) for r in decision.get("reasons", [])]

    lines: list[str] = []
    for spec in STAGES:
        report = by_agent.get(spec.agent)
        status = str(report.get("status", "missing")) if report else "missing"
        evidence = ",".join(str(p) for p in report.get("evidence", [])) if report else ""
        tier = "required" if spec.required else "advisory"
        own = [r for r in reasons if r == spec.agent or r.startswith(f"{spec.agent}:")]
        contribution = f"{tier}: " + "; ".join(own) if own else tier
        lines.append(f"{spec.agent} | {status} | {evidence} | {contribution}")
    return "\n".join(lines)


def record_override(root: Path, *, run_id: str, agent: str, reason: str, now_iso: str) -> bool:
    """Append an audited override for a warn agent in a ``review`` run.

    Returns ``False`` (and writes nothing) unless the run's verdict is
    ``review`` and the named agent's report exists with ``status == "warn"``.
    A ``halt`` verdict can therefore never be cleared here.
    """
    # Refuse path-like agent names before they reach the report filename.
    if not agent or agent != Path(agent).name or agent in {".", ".."}:
        return False

    run_dir = _run_dir(root, run_id)
    decision = _load_decision(run_dir)
    if decision is None or decision.get("verdict") != "review":
        return False
    try:
        report = json.loads((run_dir / "reports" / f"{agent}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(report, dict) or report.get("status") != "warn":
        return False

    reason = str(reason or "").strip()
    if not reason:
        return False

    entry = {"run_id": run_id, "agent": agent, "reason": reason, "now_iso": now_iso}
    with open(run_dir / "overrides.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return True


def load_overrides(root: Path, *, now_iso: str) -> list[dict]:
    """Collect today's recorded overrides for the next decision.

    An override is recorded against the run it reviews; the next run has a
    different id, so every run directory is scanned. Only entries stamped with
    the same ET date as ``now_iso`` survive (overrides expire at end of ET day)
    and each is mapped to the ``{"agent", "date", "reason"}`` shape ``decide``
    validates. Malformed lines and blank fields are skipped, never trusted.
    """
    today = _et_now(now_iso).date().isoformat()
    try:
        paths = sorted(Path(root).glob(f"{_ORCHESTRATOR_DIR.as_posix()}/*/overrides.jsonl"))
    except OSError:
        paths = []

    loaded: list[dict] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            agent = str(entry.get("agent") or "").strip()
            reason = str(entry.get("reason") or "").strip()
            if not agent or not reason:
                continue
            try:
                date = _et_now(str(entry.get("now_iso") or "")).date().isoformat()
            except ValueError:
                continue
            if date == today:
                loaded.append({"agent": agent, "date": date, "reason": reason})
    return loaded


def _resolve(value: str, *, run_id: str, symbol: str) -> str:
    """Expand the ``<run_id>`` and ``<symbol>`` stage placeholders."""
    return value.replace("<run_id>", run_id).replace("<symbol>", symbol)


def _bar_symbol(root: Path, run_id: str) -> str:
    """Prefer a bar artifact already present for this run, else the desk default."""
    try:
        existing = sorted(_run_dir(root, run_id).glob("bars_*.json"))
    except OSError:
        existing = []
    if existing:
        return existing[0].stem[len("bars_"):]
    return _DEFAULT_SYMBOL


def _artifact_path(root: Path, path: str) -> Path:
    """Resolve one artifact path, matching ``_read_artifact``'s glob policy."""
    target = Path(root) / path
    if any(ch in path for ch in "*?"):
        try:
            matches = sorted(Path(root).glob(path))
        except OSError:
            matches = []
        if matches:
            # Pick the freshest artifact, not the alphabetically last path: a
            # stage that just wrote one ticker's artifact must not be judged
            # stale against an older ticker that happens to sort later.
            def _recency(p: Path):
                try:
                    return (p.stat().st_mtime, str(p))
                except OSError:
                    return (float("-inf"), str(p))
            target = max(matches, key=_recency)
    return target


def _read_artifact(root: Path, path: str) -> dict | None:
    """Load one stage artifact; ``path`` may be a recursive glob."""
    try:
        doc = json.loads(_artifact_path(root, path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _artifact_mtime(root: Path, path: str) -> float | None:
    """Best-effort artifact mtime, used when the artifact has no embedded stamp."""
    try:
        return _artifact_path(root, path).stat().st_mtime
    except OSError:
        return None


class _ExecFailure:
    """Minimal process stand-in when the launcher itself fails."""

    def __init__(self, exc: OSError):
        self.returncode = 127
        self.stderr = f"{type(exc).__name__}: {exc}"


def _skipped_report(spec: StageSpec, *, verdict: str, now_iso: str) -> StageReport:
    """Record a stage that the decision gate chose not to execute."""
    return StageReport(
        agent=spec.agent,
        role=spec.role,
        status="skipped",
        summary=f"{spec.role}: skipped (decision was {verdict})",
        evidence=[],
        metrics={},
        started_at=now_iso,
        finished_at=now_iso,
        duration_seconds=0.0,
        exit_code=None,
        stderr_tail="",
    )


def run_slot(root: Path, run_id: str, *, slot: str, exec_cmd=subprocess.run,
             now_iso: str | None = None) -> RunDecision:
    """Execute one desk slot and record exactly one immutable run.

    ``0945`` is research-only: OrderAgent is never invoked. ``1020`` runs
    OrderAgent only when the pre-order decision is ``proceed``. The decision is
    the source of truth and is persisted only through ``write_run``; per-agent
    files are its ``reports/<agent>.json`` projections. ``now_iso`` is
    injectable for deterministic tests and defaults to the ET wall clock.
    """
    if slot not in _SLOTS:
        raise ValueError(f"unknown slot {slot!r}; expected one of {_SLOTS}")
    root = Path(root)
    now_iso = now_iso or datetime.now(_ET).isoformat()
    symbol = _bar_symbol(root, run_id)

    def run_stage(spec: StageSpec) -> StageReport:
        argv = [_resolve(arg, run_id=run_id, symbol=symbol) for arg in spec.argv]
        # The stage argv names .py files. Research stages need the LLM-capable
        # TradingAgents venv; when it is unavailable the fallback still runs and
        # any resulting error artifact now fails the stage (see artifact_error).
        if argv and argv[0].endswith(".py"):
            interpreter = sys.executable
            if getattr(spec, "python", "") == "tradingagents":
                configured = os.environ.get("TRADINGAGENTS_VENV_PYTHON", "")
                if configured:
                    candidate = Path(configured)
                    if not candidate.is_absolute():
                        candidate = root / candidate
                else:
                    ta_venv = root.parent / "TradingAgents" / ".venv"
                    candidate = ta_venv / ("Scripts/python.exe" if os.name == "nt"
                                           else "bin/python")
                if candidate.exists():
                    interpreter = str(candidate)
            argv.insert(0, interpreter)
        artifact = _resolve(spec.primary_artifact, run_id=run_id, symbol=symbol)
        try:
            proc = exec_cmd(argv, cwd=root, capture_output=True, text=True)
        except OSError as exc:  # a broken launcher fails this stage, not the run
            proc = _ExecFailure(exc)
        mtime = _artifact_mtime(root, artifact)
        return normalize(
            replace(spec, primary_artifact=artifact),
            proc=proc,
            artifacts={artifact: _read_artifact(root, artifact)},
            now_iso=now_iso,
            mtimes={artifact: mtime} if mtime is not None else None,
        )

    reports: list[StageReport] = []
    for spec in STAGES:
        if spec.agent != "OrderAgent":  # the post-decision stage is handled below
            reports.append(run_stage(spec))

    safety = read_env(root / ".env")
    try:
        # Imported here to avoid a circular import with the office viewer.
        from scripts.agent_office import trading_window
        session = trading_window()
        if not isinstance(session, dict):
            session = dict(_UNAVAILABLE_SESSION)
    except Exception:  # noqa: BLE001 - an unreadable session must fail closed
        session = dict(_UNAVAILABLE_SESSION)

    decision = decide(reports, safety=safety, session=session, now_iso=now_iso,
                      overrides=load_overrides(root, now_iso=now_iso))
    # decide() stamps an ET-derived id; the run uses one canonical id everywhere.
    decision = replace(decision, run_id=run_id)

    # The order stage runs only after the verdict is known, so append its report
    # afterwards and rebuild the decision's report list before the single write.
    # OrderAgent is advisory and cannot change the verdict; including it keeps
    # --prove honest instead of reporting a stage that did run as "missing".
    order_spec = next(spec for spec in STAGES if spec.agent == "OrderAgent")
    if slot == "1020" and decision.verdict == "proceed":
        order_report = run_stage(order_spec)
    else:
        skip_reason = decision.verdict if slot == "1020" else "0945 research-only"
        order_report = _skipped_report(order_spec, verdict=skip_reason, now_iso=now_iso)
    reports.append(order_report)
    decision = replace(decision, reports=list(reports))

    write_run(root, run_id, reports, decision)

    # ADR 0002 (approved): bind a proceeding 09:45 gate to a bounded, expiring
    # execution authorization. No-op while enforcement is disabled.
    if slot == "0945" and decision.verdict == "proceed":
        try:
            from dataclasses import asdict
            from scripts.execution_handoff import issue_for_run
            issue_for_run(root, asdict(decision))
        except Exception as exc:  # noqa: BLE001 - never break the gate for telemetry
            print(f"authorization issue failed: {type(exc).__name__}: {exc}")
    return decision


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slot", choices=_SLOTS, required=True,
                        help="which desk slot to execute (0945 is research-only)")
    parser.add_argument("--root", type=Path, default=ROOT, help="repo root (defaults to this repo)")
    args = parser.parse_args(argv)
    run_id = datetime.now(_ET).strftime("%Y%m%d-%H%M%S")
    decision = run_slot(args.root, run_id, slot=args.slot)
    print(json.dumps({"run_id": decision.run_id, "verdict": decision.verdict,
                      "reasons": decision.reasons}, indent=2))


if __name__ == "__main__":
    main()
