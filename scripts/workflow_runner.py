#!/usr/bin/env python3
"""Run one scheduled workflow stage with captured exit code and a manifest.

The scheduler must not treat a batch wrapper's exit code as proof that the
Python stage ran. This runner records start/finish, child rc, redacted output,
postcondition artifacts and a terminal status, and always propagates failure.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from robinhood_tools.workflow_manifest import (FAILED, VERIFIED,
                                               write_manifest)

ET = ZoneInfo("America/New_York")
KV_REDACT = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|bearer)\s*[:=]\s*\S+")
TOKEN_REDACT = re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{12,}"
                          r"|\bnvidia_[a-z0-9_]{20,}\b")


def redact(text: str) -> str:
    value = KV_REDACT.sub(
        lambda m: m.group(0).split(":", 1)[0].split("=", 1)[0] + "=<redacted>",
        str(text or ""))
    return TOKEN_REDACT.sub("<redacted>", value)[:4000]


def run_stage(root: Path, *, task: str, date: str, mode: str,
              command: list[str], timeout: float | None,
              expect_artifact: str | None,
              require_artifact: bool = True) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    start_epoch = time.time()
    run_id = f"{task}-{datetime.now(ET).strftime('%H%M%S')}"
    log_dir = root / "outputs" / "runs" / date
    log_dir.mkdir(parents=True, exist_ok=True)
    rc: int | None = None
    error = ""
    try:
        proc = subprocess.run(command, cwd=root, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
        rc = proc.returncode
        text = f"STDOUT:\n{redact(proc.stdout)}\nSTDERR:\n{redact(proc.stderr)}"
    except subprocess.TimeoutExpired as exc:
        rc = 124
        error = f"timeout after {timeout}s"
        text = f"TIMEOUT\n{redact(exc.stdout or '')}\n{redact(exc.stderr or '')}"
    except Exception as exc:  # noqa: BLE001 - structured failure, never silent
        rc = 127
        error = f"{type(exc).__name__}: {exc}"
        text = error
    (log_dir / f"{task}.log").write_text(text + "\n", encoding="utf-8")

    artifact_ok = not require_artifact
    if expect_artifact:
        artifact = Path(expect_artifact)
        if not artifact.is_absolute():
            artifact = root / artifact
        try:
            modified = artifact.stat().st_mtime if artifact.is_file() else 0.0
            size = artifact.stat().st_size
        except OSError:
            modified, size = 0.0, 0
        # An artifact written by an earlier session is not proof of this run.
        artifact_ok = size > 0 and modified >= start_epoch - 1.0
        if not artifact_ok:
            error = (error + "; " if error else "") + \
                f"postcondition artifact missing, empty or stale: {expect_artifact}"
    status = VERIFIED if (rc == 0 and artifact_ok and not error) else FAILED
    if status == FAILED and not rc:
        rc = 1
    finished = datetime.now(timezone.utc).isoformat()
    result = write_manifest(
        root, task=task, date=date, status=status, run_id=run_id,
        started_at=started, finished_at=finished, exit_code=rc, mode=mode,
        evidence={"command": [redact(c) for c in command],
                  "log": str(Path("outputs/runs") / date / f"{task}.log"),
                  "artifact": expect_artifact or "",
                  "artifact_ok": artifact_ok},
        error=error)
    print(json.dumps({"task": task, "status": status, "exit_code": rc,
                      "manifest": result}))
    return {"status": status, "exit_code": rc}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task", required=True)
    ap.add_argument("--date", default=datetime.now(ET).date().isoformat())
    ap.add_argument("--mode", default="paper_dry_run")
    ap.add_argument("--python", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--arg", action="append", default=[])
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--expect-artifact", default=None)
    ap.add_argument("--require-artifact", action=argparse.BooleanOptionalAction,
                    default=True)
    ap.add_argument("--root", type=Path,
                    default=Path(__file__).resolve().parents[1])
    args = ap.parse_args(argv)
    result = run_stage(
        Path(args.root), task=args.task, date=args.date, mode=args.mode,
        command=[args.python, args.script, *args.arg],
        timeout=args.timeout, expect_artifact=args.expect_artifact,
        require_artifact=args.require_artifact)
    return 0 if result["status"] == VERIFIED else int(result["exit_code"] or 1)


if __name__ == "__main__":
    raise SystemExit(main())
