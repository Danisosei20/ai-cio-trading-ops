#!/usr/bin/env python3
"""Slack notification sender: post a paper-desk update to the configured channel.

  python3 scripts/notify.py --send "Bought 2 NVDA @ 237. Reason: ..."

Sends are plain notifications and create no trading authority. There is no
reply monitor: the desk does not read, parse, or act on Slack replies.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

THREAD_FILE = Path("outputs/paper/slack_thread.json")


def send(text: str, channel_id: str = "") -> dict:
    """Post a message to Slack and record the last message sent."""
    from robinhood_tools.settings import load_env

    env = {**load_env(".env"), **os.environ}
    channel_id = channel_id or env.get("SLACK_CHANNEL_ID", "")
    if not channel_id:
        raise RuntimeError("SLACK_CHANNEL_ID is required")

    import json as _json
    import urllib.request
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=_json.dumps({"channel": channel_id, "text": text}).encode(),
        headers={
            "Authorization": f"Bearer {env['SLACK_BOT_TOKEN']}",
            "Content-Type": "application/json",
        },
        method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = _json.loads(resp.read() or b"{}")
    record = {
        "channel_id": channel_id,
        "message_ts": result.get("ts", ""),
        "text": text,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    THREAD_FILE.parent.mkdir(parents=True, exist_ok=True)
    THREAD_FILE.write_text(json.dumps(record, indent=2))
    print(f"sent to {channel_id} ts={result.get('ts', '')}")
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Slack notification sender.")
    ap.add_argument("--send", default="", help="message text to post")
    ap.add_argument("--channel", default="", help="override SLACK_CHANNEL_ID")
    a = ap.parse_args(argv)

    if a.send:
        send(a.send, channel_id=a.channel)
        return 0

    print("usage: notify.py --send 'text'")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
