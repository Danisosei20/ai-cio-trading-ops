"""Decision engine for the paper-desk orchestrator.

The decision is the single go/no-go gate for a run. It is deliberately
fail-closed: only a fully paper-only safety snapshot, a tradeable session, and
``ok`` reports from every required stage can produce ``proceed``. Overrides can
downgrade a ``review`` to ``proceed`` but can never clear a ``halt``.

Storage (``write_run`` / ``read_latest`` / ``prove`` / ``record_override``) and
the runner (``run_slot``) are added to this module by later tasks.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.desk_stage_agents import STAGES, StageReport

_ET = ZoneInfo("America/New_York")
_REQUIRED_AGENTS = tuple(spec.agent for spec in STAGES if spec.required)


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
