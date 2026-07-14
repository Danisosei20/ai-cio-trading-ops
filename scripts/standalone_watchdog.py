#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib import request
from zoneinfo import ZoneInfo


LAST_RUN = re.compile(r"^Last run:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", re.MULTILINE)
TEST_MESSAGE = (
    "TEST ONLY — AI CIO health-route check passed. No review was missed, "
    "no trade was recommended, and no action is required."
)


def last_completed_at(memory_path: Path, timezone_name: str) -> datetime | None:
    if not memory_path.exists():
        return None
    match = LAST_RUN.search(memory_path.read_text(encoding="utf-8"))
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(timezone_name))


def scheduled_datetime(day, scheduled_time: str, timezone_name: str) -> datetime:
    hour, minute = map(int, scheduled_time.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(timezone_name))


def send_slack(message: str, *, channel_id: str, keychain_service: str, keychain_account: str) -> None:
    token = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-w", "-s", keychain_service, "-a", keychain_account],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    payload = json.dumps({"channel": channel_id, "text": message}).encode("utf-8")
    api_request = request.Request(
        "https://slack.com/api/chat.postMessage", data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(api_request, timeout=15) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("ok") is not True:
        raise RuntimeError(f"Slack health alert failed ({result.get('error', 'unknown_error')}).")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Independent AI CIO completion watchdog.")
    parser.add_argument("--memory", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--health-channel", required=True)
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--scheduled-time", default="09:45")
    parser.add_argument("--grace-minutes", type=int, default=15)
    parser.add_argument("--automation-id", default="ai-cio-daily-review")
    parser.add_argument("--keychain-service", default="openai.ai-cio-watchdog.slack")
    parser.add_argument("--keychain-account", default="ai-cio-watchdog")
    parser.add_argument("--now", help="ISO timestamp used only for deterministic testing.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--test-alert", action="store_true",
        help="Send an unmistakable non-trading health-route test without changing watchdog state.",
    )
    args = parser.parse_args(argv)

    if args.test_alert:
        if args.dry_run:
            print(json.dumps({"state": "test_alert_ready", "message": TEST_MESSAGE}))
            return 0
        try:
            send_slack(
                TEST_MESSAGE, channel_id=args.health_channel, keychain_service=args.keychain_service,
                keychain_account=args.keychain_account,
            )
        except Exception as exc:
            error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            print(json.dumps({"state": "test_alert_failed", "error": error}), file=sys.stderr)
            return 3
        print(json.dumps({"state": "test_alert_sent"}))
        return 0

    tz = ZoneInfo(args.timezone)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    scheduled = scheduled_datetime(now.astimezone(tz).date(), args.scheduled_time, args.timezone)
    if now < scheduled + timedelta(minutes=args.grace_minutes):
        print(json.dumps({"state": "not_due", "scheduled_for": scheduled.isoformat()}))
        return 0

    completed = last_completed_at(Path(args.memory).expanduser(), args.timezone)
    if completed and completed.astimezone(tz).date() == scheduled.date() and completed >= scheduled:
        print(json.dumps({"state": "healthy", "completed_at": completed.isoformat()}))
        return 0

    state_path = Path(args.state_file).expanduser()
    alert_key = f"{args.automation_id}:{scheduled.date().isoformat()}"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if state.get("last_alert_key") == alert_key:
        print(json.dumps({"state": "already_alerted", "alert_key": alert_key}))
        return 0

    detail = "never" if completed is None else completed.isoformat()
    message = (
        f"AI CIO health alert: the {args.automation_id} review scheduled for "
        f"{scheduled.isoformat()} has not completed. Last completion: {detail}. Manual review required."
    )
    if args.dry_run:
        print(json.dumps({"state": "missed", "alert_key": alert_key, "message": message}))
        return 2
    try:
        send_slack(
            message, channel_id=args.health_channel, keychain_service=args.keychain_service,
            keychain_account=args.keychain_account,
        )
    except Exception as exc:
        print(json.dumps({"state": "alert_failed", "error": type(exc).__name__}), file=sys.stderr)
        return 3
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_alert_key": alert_key}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"state": "alert_sent", "alert_key": alert_key}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
