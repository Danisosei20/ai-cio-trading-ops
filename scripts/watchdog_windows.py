#!/usr/bin/env python3
"""Windows watchdog for the scheduled 09:45 ET auto-trade run.

Reads outputs/auto-trade-run.json (written by auto_trade_weekday.bat):

    ok        run finished today
    running   started <45m ago and not finished (LLM research takes 20-30 min)
    stalled   started >45m ago with no finish
    missed    no start today, after the 09:46 ET window

One alert per ET day per state class (outputs/watchdog-state.json), so a
stalled run pages you once, not every check. Weekends (ET) are silent per
desk policy. Slack via scripts.notify.send (repo .env credentials).
Never trades; notification-only. Usage:
    python scripts/watchdog_windows.py            # check (+ alert if needed)
    python scripts/watchdog_windows.py --dry-run  # evaluate and print only
    python scripts/watchdog_windows.py --test     # labeled route test (sends)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
STATE_FILE = ROOT / "outputs" / "watchdog-state.json"
RUN_FILE = ROOT / "outputs" / "auto-trade-run.json"
STALL_MINUTES = 45
TEST_MESSAGE = ("TEST ONLY - AI CIO watchdog route check passed. No run was "
                "missed, no trade was recommended, and no action is required.")


def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def evaluate(now: datetime) -> tuple[str, str]:
    data = _read_json(RUN_FILE, {})
    started = _parse(data.get("started_at"))
    finished = _parse(data.get("finished_at"))
    today = now.date().isoformat()
    started_today = started is not None and started.date().isoformat() == today
    finished_today = finished is not None and finished.date().isoformat() == today
    if started_today and finished_today:
        return "ok", "run finished today"
    if started_today:
        age = (now - started).total_seconds() / 60
        if age <= STALL_MINUTES:
            return "running", f"started {age:.0f}m ago, inside {STALL_MINUTES}m grace"
        return "stalled", f"started {age:.0f}m ago with no finish (stall > {STALL_MINUTES}m)"
    if now.hour * 60 + now.minute >= 9 * 60 + 46:
        return "missed", "no run started today after the 09:45 ET window"
    return "waiting", "before 09:46 ET; nothing to check yet"


def _alert(message: str, *, force: bool = False) -> None:
    from scripts.notify import send

    send(message, force_weekend=force)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Windows watchdog for the 09:45 ET auto-trade run.")
    p.add_argument("--test", action="store_true", help="send a labeled route test; no state change")
    p.add_argument("--dry-run", action="store_true", help="evaluate and print, never send")
    args = p.parse_args(argv)

    now = datetime.now(ET)

    if args.test:
        if args.dry_run:
            print(json.dumps({"state": "test_alert_ready", "message": TEST_MESSAGE}))
            return 0
        try:
            _alert(TEST_MESSAGE, force=True)
            print("watchdog: test alert sent")
        except Exception as exc:  # noqa: BLE001 - route proof must not crash the shell
            print(f"watchdog: test alert FAILED ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
            return 3
        return 0

    if now.weekday() >= 5:
        print("watchdog: weekend (ET) - desk mute, nothing to check")
        return 0

    status, reason = evaluate(now)
    print(f"watchdog: {status} - {reason}")
    if status in ("ok", "running", "waiting"):
        return 0

    memory = _read_json(STATE_FILE, {})
    key = f"{now.date().isoformat()}:{status}"
    if memory.get("last_alert") == key:
        print("watchdog: already alerted for this state today; staying quiet")
        return 0

    message = (f"*AI CIO watchdog* - the 09:45 ET auto-trade run is "
               f"*{status.upper()}* ({reason}). Check outputs/auto-trade.log "
               "on the desk machine.")
    if args.dry_run:
        print(f"watchdog: would alert - {message}")
        return 0

    try:
        _alert(message)
        memory["last_alert"] = key
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(memory), encoding="utf-8")
        print("watchdog: alert sent")
    except Exception as exc:  # noqa: BLE001 - alert failure must not crash the task
        print(f"watchdog: alert FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
