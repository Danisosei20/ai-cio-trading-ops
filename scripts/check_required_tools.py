#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robinhood_tools.settings import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Check configured approval route tool requirements.")
    parser.add_argument("--config", default="config/approval_routes.json")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--available-tools",
        default="",
        help="Comma-separated tool names currently exposed by the host.",
    )
    args = parser.parse_args()

    config = load_config(args.config, args.env_file)
    available = {tool.strip() for tool in args.available_tools.split(",") if tool.strip()}

    missing: list[str] = []
    slack = config.get("channels", {}).get("slack", {})
    if slack.get("enabled"):
        required = slack.get("required_tools", [])
        missing = [tool for tool in required if tool not in available]

    if missing:
        print("missing required tools: " + ", ".join(missing))
        return 2

    print("required approval tools available")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
