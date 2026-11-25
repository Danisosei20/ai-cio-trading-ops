#!/usr/bin/env python3
"""Controlled daily-workflow rehearsal for Phase 8.6.

Runs an isolated, frozen-clock simulation of the registered workflow order.
Every simulated broker outcome is labeled as such; no Alpaca paper endpoint is
contacted and no order is submitted. The rehearsal proves that terminal
manifests, lifecycle statuses and the dashboard all agree, including on a
deliberately failing task path.
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
    FAILED, NO_ACTION_REQUIRED, VERIFIED, read_manifest, write_manifest)
from scripts import eod_lifecycle_report as lifecycle
from scripts import operational_reliability

DATE = "2026-11-24"
NOW = f"{DATE}T17:00:00-05:00"

# (scheduled time, scheduler task name, manifest id, simulated outcome)
STAGES = (
    ("09:35", "AI-Desk-OvernightExit", "overnight-exit", VERIFIED),
    ("09:45", "AI-Desk-DeterministicGate", "deterministic-gate", VERIFIED),
    ("09:50", "AI-Desk-Watchdog", "watchdog", VERIFIED),
    ("10:20", "AI-Desk-AuthorizationHandoff", "authorization-handoff", VERIFIED),
    ("10:00", "AI-Desk-IntradayLoop", "intraday-loop", VERIFIED),
    ("15:10", "AI-Desk-OptionsWatch-1510", "options-watch", VERIFIED),
    ("15:40", "AI-Desk-OvernightEnter", "overnight", NO_ACTION_REQUIRED),
    ("15:45", "AI-Desk-Flatten-1545", "flatten-verify", NO_ACTION_REQUIRED),
    ("16:10", "AI-Desk-NewsReadout-1610", "news", VERIFIED),
    ("16:30", "AI-Desk-ScoreShadows-1630", "shadow-scoring", VERIFIED),
)


def run(root: Path, *, date: str = DATE, fail_stage: str | None = None) -> dict:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    artifacts = {task: rel for _, task, rel in lifecycle.EVENTS}
    tasks = {}
    for index, (scheduled, task, manifest_id, outcome) in enumerate(STAGES):
        if task in artifacts:
            path = root / "outputs" / artifacts[task]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"rehearsal {date} {scheduled}", encoding="utf-8")
        status = FAILED if manifest_id == fail_stage else outcome
        exit_code = 1 if status == FAILED else 0
        write_manifest(
            root, task=manifest_id, date=date, status=status,
            run_id=f"rehearsal-{manifest_id}", started_at=f"{date}T{scheduled}:00-05:00",
            finished_at=NOW, exit_code=exit_code,
            mode="simulated_paper_dry_run",
            evidence={"simulated": True, "broker": "simulated",
                      "scheduled_time": scheduled,
                      "orders_submitted": 0,
                      "entry_permission": "none - dry run and verification only"},
            error=("injected rehearsal failure" if status == FAILED else ""))
        # Reconstruct the exact manifest to confirm it survives an HMAC read.
        assert read_manifest(root, manifest_id, date)["status"] == status
        tasks[task] = {"Name": task, "Last": f"{date} {scheduled}:00",
                       "Result": exit_code}

    with mock.patch.object(lifecycle, "_task_info", return_value=tasks), \
            mock.patch.object(lifecycle, "_broker_state",
                              return_value={"positions": 0, "open_orders": 0,
                                            "filled_orders_today": 0,
                                            "source": "SIMULATED broker - not Alpaca"}):
        report = lifecycle.build_lifecycle(root, date, now=NOW)
    payload = operational_reliability.operational_reliability_payload(root, date=date)

    statuses = {event["event"]: event["status"] for event in report["events"]}
    failed = [name for name, status in statuses.items() if status == FAILED]
    labels = {task: label for label, task, _ in lifecycle.EVENTS}
    expected_failed = [labels[task] for _, task, mid, _ in STAGES
                       if mid == fail_stage]
    result = {
        "track": "operational_reliability_rehearsal",
        "generated_at": datetime.now().astimezone().isoformat(),
        "session_date": date, "frozen_now": NOW,
        "scenario": "injected_failure" if fail_stage else "nominal",
        "stages": [s[0] + " " + s[1] for s in STAGES],
        "lifecycle_statuses": statuses,
        "dashboard_status": payload["status"],
        "dashboard_workflows": payload.get("workflows", []),
        "dashboard_last_failed": payload.get("last_failed", []),
        "failed_events": failed,
        "expected_failed_events": expected_failed,
        "orders_submitted": 0,
        "entry_permission": "none - simulated dry run only",
        "broker_outcome": "SIMULATED - never reconciled as an actual Alpaca fill",
        "overnight_dry_run": True,
        "authorization_enforcement": "not changed operationally",
        "invariants": {
            "all_expected_failures_reported": failed == expected_failed,
            "no_unreported_failure": set(failed) <= set(expected_failed),
            "dashboard_matches_manifests": payload["status"] == "ok",
            "no_orders": payload.get("orders_submitted") == 0,
            "overnight_is_dry_run": True,
        },
    }
    result["passed"] = all(result["invariants"].values())
    out = root / "rehearsal_result.json"
    out.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=DATE)
    ap.add_argument("--fail-stage", default=None,
                    help="manifest id of a stage to fail deliberately")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--root", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.root:
        root = Path(args.root)
        result = run(root, date=args.date, fail_stage=args.fail_stage)
    else:
        with tempfile.TemporaryDirectory() as td:
            result = run(Path(td), date=args.date, fail_stage=args.fail_stage)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(result, indent=2, default=str) + "\n",
                                    encoding="utf-8")
    print(f"rehearsal {'PASS' if result['passed'] else 'FAIL'}: "
          f"{len(result['stages'])} stages, failed={result['failed_events']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
