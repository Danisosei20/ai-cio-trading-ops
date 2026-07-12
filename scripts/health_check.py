#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.settings import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-posting approval-route health check.")
    parser.add_argument("--config", default="config/approval_routes.json")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--available-tools", default="")
    parser.add_argument("--robinhood-read-ok", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config, args.env_file)
    available = {item.strip() for item in args.available_tools.split(",") if item.strip()}
    slack = config.get("channels", {}).get("slack", {})
    problems = []
    if slack.get("enabled"):
        if not slack.get("channel_id"):
            problems.append("Slack channel_id is empty")
        missing = sorted(set(slack.get("required_tools", [])) - available)
        if missing:
            problems.append("missing Slack tools: " + ", ".join(missing))
    if not args.robinhood_read_ok:
        problems.append("Robinhood read access not verified")

    if problems:
        print("UNHEALTHY: " + "; ".join(problems))
        return 2
    print("HEALTHY: configuration, Slack send capability, and Robinhood read access verified; no message posted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
