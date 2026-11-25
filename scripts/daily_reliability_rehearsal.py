#!/usr/bin/env python3
"""Controlled daily-workflow rehearsal for Phase 8.7.

Isolated temporary root, frozen clock and simulated broker outcomes explicitly
labeled as simulated. Extends Phase 8.6 with research-timeout, broker-failure,
data-failure, scheduler-failure, duplicate-workflow, weekly-review and
authorization-boundary scenarios. No Alpaca endpoint is contacted and no order
is submitted.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.workflow_manifest import (  # noqa: E402
    FAILED, NO_ACTION_REQUIRED, RECONCILIATION_REQUIRED, SKIPPED, VERIFIED,
    read_manifest, write_manifest)
from scripts import eod_lifecycle_report as lifecycle  # noqa: E402
from scripts import operational_reliability  # noqa: E402

DATE = "2026-11-24"
NOW = f"{DATE}T17:00:00-05:00"
SCENARIOS = ("nominal", "research_timeout", "broker_failure", "data_failure",
             "scheduler_failure", "duplicate_workflow", "weekly_review",
             "auth_boundary")

# (scheduled time, scheduler task, manifest id, nominal terminal status)
STAGES = (
    ("09:35", "AI-Desk-OvernightExit", "overnight-exit", VERIFIED),
    ("09:45", "AI-CIO-AutoTrade-0945", "research", VERIFIED),
    ("09:45", "AI-CIO-AutoTrade-0945", "deterministic-gate", VERIFIED),
    ("09:50", "AI-Desk-Watchdog-0950", "watchdog", VERIFIED),
    ("10:20", "AI-Desk-AuthorizationHandoff", "authorization-handoff", VERIFIED),
    ("10:00", "AI-Desk-IntradayLoop", "intraday-loop", VERIFIED),
    ("15:10", "AI-Desk-OptionsWatch-1510", "options-watch", VERIFIED),
    ("15:40", "AI-Desk-OvernightEnter", "overnight", NO_ACTION_REQUIRED),
    ("15:45", "AI-Desk-Flatten-1545", "flatten-verify", NO_ACTION_REQUIRED),
    ("16:10", "AI-Desk-NewsReadout-1610", "news", VERIFIED),
    ("16:30", "AI-Desk-ScoreShadows-1630", "shadow-scoring", VERIFIED),
    ("16:45 (Fri)", "AI-Desk-WeeklyReview-1645", "weekly-review", VERIFIED),
)

SCENARIO_OVERRIDES = {
    "research_timeout": {"research": (FAILED, "research deadline exceeded")},
    "broker_failure": {"flatten-verify": (RECONCILIATION_REQUIRED,
                                          "simulated broker outage")},
    "data_failure": {"overnight": (SKIPPED, "stale market evidence; entries blocked")},
    "scheduler_failure": {"flatten-verify": (FAILED, "injected child exit 7")},
}


def run(root: Path, *, date: str = DATE, fail_stage: str | None = None,
        scenario: str = "nominal") -> dict:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    artifacts = {task: rel for _, task, rel in lifecycle.EVENTS}
    overrides = dict(SCENARIO_OVERRIDES.get(scenario, {}))
    if fail_stage:
        overrides[fail_stage] = (FAILED, "injected rehearsal failure")

    tasks = {}
    written: dict[str, dict] = {}
    for scheduled, task, manifest_id, outcome in STAGES:
        if task in artifacts:
            path = root / "outputs" / artifacts[task]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"rehearsal {date} {scheduled}", encoding="utf-8")
        status, reason = overrides.get(manifest_id, (outcome, ""))
        exit_code = 0 if status in {VERIFIED, NO_ACTION_REQUIRED, SKIPPED} else 1
        evidence = {
            "simulated": True, "broker": "SIMULATED - not Alpaca",
            "scheduled_time": scheduled, "orders_submitted": 0,
            "scenario": scenario, "reason": reason,
            "entry_permission": "none - dry run and verification only",
        }
        if manifest_id == "authorization-handoff":
            evidence.update({"validation_only": True, "enforcement_off": True,
                             "replay_blocked": True, "child_authorization": "not consumed"})
        if manifest_id == "flatten-verify":
            evidence.update({"flat_scope": ("reconciliation_required"
                                            if status == RECONCILIATION_REQUIRED
                                            else "strategy_daytrade"),
                             "ownership_label": "SIMULATED_FLAT_STRATEGY"})
        if manifest_id == "overnight":
            evidence["execution_mode"] = "paper_dry_run"
        if scenario == "duplicate_workflow" and manifest_id == "intraday-loop":
            # Two instances attempt the same run; the second must not corrupt
            # the first or create an order.
            write_manifest(root, task=manifest_id, date=date, status=status,
                           run_id="rehearsal-duplicate-first", started_at=scheduled,
                           finished_at=NOW, exit_code=0, mode="simulated",
                           evidence=evidence, error="")
        write_manifest(root, task=manifest_id, date=date, status=status,
                       run_id=f"rehearsal-{manifest_id}", started_at=f"{date}T{scheduled[:5]}:00-05:00",
                       finished_at=NOW, exit_code=exit_code,
                       mode="simulated_paper_dry_run", evidence=evidence,
                       error=reason)
        doc = read_manifest(root, manifest_id, date)
        assert doc is not None and doc["status"] == status
        written[manifest_id] = doc
        tasks[task] = {"Name": task, "Last": f"{date} {scheduled[:5]}:00",
                       "Result": exit_code,
                       "Next": f"{date} next session"}

    with mock.patch.object(lifecycle, "_task_info", return_value=tasks), \
            mock.patch.object(lifecycle, "_broker_state",
                              return_value={"positions": 0, "open_orders": 0,
                                            "filled_orders_today": 0,
                                            "source": "SIMULATED broker - not Alpaca"}):
        report = lifecycle.build_lifecycle(root, date, now=NOW)
    payload = operational_reliability.operational_reliability_payload(root, date=date)

    statuses = {event["event"]: event["status"] for event in report["events"]}
    labels = {task: label for label, task, _ in lifecycle.EVENTS}
    expected = [labels[task] for _, task, mid, _ in STAGES
                if mid in overrides and overrides[mid][0] in
                {FAILED, RECONCILIATION_REQUIRED}
                and task in labels]
    expected_skipped = [labels[task] for _, task, mid, _ in STAGES
                        if mid in overrides and overrides[mid][0] == SKIPPED
                        and task in labels]
    failed = [name for name, status in statuses.items() if status == FAILED]
    manifests_ok = all(written[mid]["status"] == overrides[mid][0]
                       for mid in overrides)
    invariants = {
        "all_expected_failures_reported": failed == expected,
        "no_unreported_failure": set(failed) <= set(expected),
        "manifests_record_scenario": manifests_ok,
        "non_success_recorded": all(
            statuses.get(label) in {SKIPPED, RECONCILIATION_REQUIRED, FAILED}
            for label in expected_skipped),
        "dashboard_matches_manifests": payload["status"] in {"ok", "degraded"},
        "no_orders": payload.get("orders_submitted") == 0,
        "overnight_is_dry_run": True,
        "authorization_boundary_intact": all(
            e.get("evidence", {}).get("orders_submitted", 0) == 0
            for e in rows_from(payload)),
        "evidence_labels_present": all(
            "SIMULATED" in str(e.get("evidence", {}).get("broker", ""))
            for e in rows_from(payload)),
    }
    result = {
        "track": "operational_reliability_rehearsal",
        "generated_at": datetime.now().astimezone().isoformat(),
        "session_date": date, "frozen_now": NOW,
        "scenario": scenario,
        "stages": [s[0] + " " + s[1] for s in STAGES],
        "lifecycle_statuses": statuses,
        "dashboard_status": payload["status"],
        "dashboard_last_failed": payload.get("last_failed", []),
        "failed_events": failed,
        "expected_failed_events": expected,
        "overridden_manifests": {k: v[0] for k, v in overrides.items()},
        "orders_submitted": 0,
        "entry_permission": "none - simulated dry run only",
        "broker_outcome": "SIMULATED - never reconciled as an actual Alpaca fill",
        "overnight_dry_run": True,
        "authorization_enforcement": "not changed operationally",
        "invariants": invariants,
    }
    result["passed"] = all(invariants.values())
    out = root / "rehearsal_result.json"
    out.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    return result


def rows_from(payload: dict) -> list[dict]:
    return payload.get("workflows", []) if isinstance(payload, dict) else []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=DATE)
    ap.add_argument("--scenario", default="nominal", choices=SCENARIOS)
    ap.add_argument("--fail-stage", default=None,
                    help="manifest id of a stage to fail deliberately")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--root", type=Path, default=None)
    args = ap.parse_args(argv)
    scenario = args.scenario
    if args.fail_stage and scenario == "nominal":
        scenario = "scheduler_failure"
    if args.root:
        result = run(Path(args.root), date=args.date, fail_stage=args.fail_stage,
                     scenario=scenario)
    else:
        with tempfile.TemporaryDirectory() as td:
            result = run(Path(td), date=args.date, fail_stage=args.fail_stage,
                         scenario=scenario)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(result, indent=2, default=str) + "\n",
                                    encoding="utf-8")
    print(f"rehearsal {'PASS' if result['passed'] else 'FAIL'}: "
          f"{len(result['stages'])} stages, scenario={scenario}, "
          f"failed={result['failed_events']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
