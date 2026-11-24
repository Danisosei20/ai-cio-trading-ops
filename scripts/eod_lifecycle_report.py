#!/usr/bin/env python3
"""Read-only end-of-day lifecycle verification from real artifacts.

Each scheduled event is reported as Scheduled / Started / Completed / Failed /
Skipped / Not verified. A registered task is never treated as proof of
execution, and a morning zero-order state is never extrapolated to the full
day. No orders, no writes outside the generated report.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

EVENTS = (
    ("15:10 options watch", "AI-Desk-OptionsWatch-1510", "options-watch.log"),
    ("15:40 overnight dry-run", "AI-Desk-OvernightEnter", "overnight.log"),
    ("15:45 intraday flatten", "AI-Desk-Flatten-1545", "flatten.log"),
    ("16:10 news readout", "AI-Desk-NewsReadout-1610", "news/explanations"),
    ("16:30 shadow scoring", "AI-Desk-ScoreShadows-1630", "score-shadow.log"),
)


def _task_info() -> dict[str, dict]:
    script = ("Get-ScheduledTask | Where-Object { $_.TaskName -like 'AI-*' } | "
              "ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
              "[pscustomobject]@{Name=$_.TaskName; Last=$i.LastRunTime; "
              "Result=$i.LastTaskResult; Next=$i.NextRunTime} } | ConvertTo-Json -Compress")
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy",
                               "Bypass", "-Command", script],
                              capture_output=True, text=True, timeout=60)
        payload = json.loads(proc.stdout.strip() or "[]")
    except Exception:  # noqa: BLE001 - unverified is an acceptable result
        return {}
    if isinstance(payload, dict):
        payload = [payload]
    return {str(item.get("Name", "")): item for item in payload if isinstance(item, dict)}


def _dated_today(value: object, date: str) -> bool:
    """Accept both ISO (2026-11-24...) and Windows US (11/24/2026...) stamps."""
    text = str(value or "")
    if not text:
        return False
    if date in text:
        return True
    try:
        year, month, day = (int(part) for part in date.split("-"))
        return datetime.strptime(text.strip(), "%m/%d/%Y %I:%M:%S %p").date() == \
            datetime(year, month, day).date()
    except (ValueError, TypeError):
        return False


def _artifact_state(root: Path, rel: str, date: str) -> dict:
    path = root / "outputs" / rel
    if path.is_dir():
        matches = [p for p in path.glob(f"{date}*") if p.is_file()]
        latest = max(matches, key=lambda p: p.stat().st_mtime) if matches else None
    else:
        latest = path if path.is_file() else None
    if latest is None:
        return {"status": "not_verified", "path": rel, "timestamp": None}
    try:
        modified = datetime.fromtimestamp(latest.stat().st_mtime, ET).isoformat()
    except OSError:
        return {"status": "not_verified", "path": rel, "timestamp": None}
    stamp = latest.read_text(encoding="utf-8", errors="replace")[:4000]
    return {"status": "artifact_present", "path": str(latest.relative_to(root)),
            "timestamp": modified, "dated_today": date in modified,
            "mentions_today": date in stamp}


def _broker_state(root: Path, date: str) -> dict:
    try:
        from robinhood_tools.alpaca_paper import (  # noqa: PLC0415
            AlpacaPaperBackend, AlpacaPaperHttpTransport)
        from robinhood_tools.settings import load_env  # noqa: PLC0415
        env = {**load_env(str(root / ".env")), **__import__("os").environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        return {
            "positions": len(backend.list_positions()),
            "open_orders": len(backend.list_open_orders()),
            "clock": backend.market_clock(),
            "source": "read-only Alpaca paper GET",
        }
    except Exception as exc:  # noqa: BLE001 - never fail open
        return {"status": "not_verified", "reason": f"{type(exc).__name__}: {exc}"}


def build_lifecycle(root: Path, date: str, *, now: str | None = None) -> dict:
    stamp = now or datetime.now(ET).isoformat()
    tasks = _task_info()
    events = []
    for label, task_name, artifact in EVENTS:
        info = tasks.get(task_name) or {}
        art = _artifact_state(root, artifact, date)
        last = str(info.get("Last") or "")
        if _dated_today(last, date) and str(info.get("Result")) == "0":
            schedule_state = "Completed"
        elif _dated_today(last, date):
            schedule_state = "Failed"
        else:
            schedule_state = "Not verified"
        events.append({
            "event": label,
            "scheduled_state": schedule_state,
            "scheduler_last_run": last or None,
            "scheduler_result": info.get("Result"),
            "next_run": info.get("Next") or None,
            "artifact": art,
            "verdict": ("Completed" if schedule_state == "Completed" and
                        art.get("dated_today") else "Not verified"),
        })
    return {
        "track": "eod_lifecycle",
        "generated_at": stamp,
        "session_date": date,
        "session_complete": datetime.fromisoformat(
            stamp.replace("Z", "+00:00")).astimezone(ET).hour >= 16
        if stamp else False,
        "orders_submitted": 0,
        "entry_permission": "none - verification only",
        "events": events,
        "broker": _broker_state(root, date),
        "note": ("A scheduled task is not proof of execution. Zero morning orders "
                 "does not establish zero orders for the full session."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    report = build_lifecycle(args.root, args.date)
    text = json.dumps(report, indent=2, default=str) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
