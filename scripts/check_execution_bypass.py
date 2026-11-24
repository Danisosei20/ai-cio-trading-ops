#!/usr/bin/env python3
"""CI guard: fail on NEW direct entry-order submission paths.

Known submission sites are allowlisted (see docs/architecture/execution-path-audit.md).
Any new file calling a broker order endpoint or service directly must either use
the execution gateway or update the allowlist with an explicit owner review.
"""
from __future__ import annotations

import re
from pathlib import Path

ALLOWED_FILES = {
    "robinhood_tools/alpaca_paper.py",
    "robinhood_tools/alpaca_options.py",
    "robinhood_tools/service.py",
    "robinhood_tools/paper_flow.py",
    "robinhood_tools/options_flow.py",
    "robinhood_tools/paper_autonomy.py",
    "robinhood_tools/workflow.py",
    "robinhood_tools/paper_autopilot.py",
    "scripts/daytrade_engine.py",
    "scripts/overnight_pilot.py",
    "scripts/flatten_daytrades.py",
    "scripts/paper_position_guard.py",
    "scripts/options_guard.py",
    "scripts/options_pilot.py",
    "scripts/alert_webhook.py",
    "scripts/tradingagents_paper_auto.py",
    "scripts/intraday_loop.py",
    "scripts/session_rehearsal.py",
    "scripts/scenario_test.py",
    "scripts/paper_certification.py",
    "scripts/daily_controls.py",
    # Reviewed infrastructure / test adapters and read-only order queries:
    "robinhood_tools/client.py",          # backend Protocol method definitions
    "robinhood_tools/mcp_backend.py",     # live MCP adapter (human approval required)
    "robinhood_tools/paper.py",           # simulator
    "robinhood_tools/sim_broker.py",      # simulator
    "scripts/check_execution_bypass.py",  # this guard's own pattern literal
    "scripts/weekly_scorecard.py",        # GET /v2/orders read only
    "robinhood_tools/health.py",           # outbound status webhook, not an order
    "robinhood_tools/slack_web_api.py",    # Slack webhook, not an order
    "scripts/news_explainer.py",           # NVIDIA NIM chat POST, not an order
    "scripts/standalone_watchdog.py",      # alert webhook, not an order
}
ALLOWED_LINES = {
    ("robinhood_tools/alpaca_paper.py", 139),
    ("robinhood_tools/alpaca_options.py", 265),
}
PATTERNS = re.compile(
    r"(place_equity_order\s*\(|place_option_order\s*\(|/v2/ord|"
    r"\b(?:client|session|requests|httpx|http)\.post\s*\(|"
    r"\bpost\s*\([^)]*(?:url|orders|path)|"
    r"transport\.request\s*\(\s*(?:[\"']POST|method)|"
    r"getattr\s*\([^)]*[\"']request[\"']\s*\)|"
    r"[\"']POST[\"']\s*,|"
    r"curl\b.*-X\s*POST|subprocess\.(?:run|Popen)\([^)]*curl)", re.I)
SKIP_DIRS = {"tests", "__pycache__", ".venv", "outputs", ".git"}


def is_allowlisted(rel: str, line: int) -> bool:
    rel = rel.replace("\\", "/")
    return rel in ALLOWED_FILES or (rel, line) in ALLOWED_LINES


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in Path(root).rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            rel = str(path.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        if is_allowlisted(rel, 0):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if PATTERNS.search(line):
                findings.append(f"{rel}:{number}: direct order submission")
    return sorted(findings)


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[1]
    findings = scan(root)
    if findings:
        print("execution bypass guard: FAIL")
        for finding in findings:
            print("  " + finding)
        return 2
    print(f"execution bypass guard: PASS ({len(ALLOWED_FILES)} allowlisted modules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
