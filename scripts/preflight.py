#!/usr/bin/env python3
"""Monday-morning preflight: is the unattended desk actually ready?

Run before the first human-free session (or any Monday). Prints PASS/WARN/FAIL
per check and exits with the FAIL count. Nothing here trades; the broker check
is read-only account access.

Checks:
  1. Scheduled tasks registered (AI-CIO-AutoTrade-0945, AI-Desk-Watchdog-0950)
  2. repo .env: Alpaca paper keys + Slack credentials
  3. TradingAgents/.env: NVIDIA_API_KEY (FAIL - research cannot run without it)
                        FRED_API_KEY (WARN - macro analyst is blind without it)
  4. outputs junction still resolves (outputs/paper reachable via the repo path)
  5. TradingAgents venv python exists and imports tradingagents
  6. Alpaca paper account readable (read-only network check)
  7. Office viewer answering on 127.0.0.1:8766 (WARN - cosmetic only)
  --full additionally runs the unit test suite (slow).

Usage:
  python scripts/preflight.py          # full checks
  python scripts/preflight.py --full   # + unit tests
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO_ENV = ROOT / ".env"
TA_ENV = ROOT.parent / "TradingAgents" / ".env"
TA_PY = ROOT.parent / "TradingAgents" / ".venv" / "Scripts" / "python.exe"
TASKS = ("AI-CIO-AutoTrade-0945", "AI-Desk-Watchdog-0950",
         "AI-Desk-ScoreShadows-1630", "AI-Desk-OptionsWatch-1510",
         "AI-Desk-WeeklyReview-1645", "AI-Desk-DayTrade-1030",
         "AI-Desk-Flatten-1545")

results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))


def _env_value(path: Path, key: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def check_tasks() -> None:
    missing = []
    for name in TASKS:
        proc = subprocess.run(["schtasks", "/Query", "/TN", name, "/FO", "LIST"],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            missing.append(name)
    if missing:
        record("FAIL", "scheduled tasks", f"missing: {', '.join(missing)}")
    else:
        record("PASS", "scheduled tasks", f"{len(TASKS)} registered")


def check_repo_env() -> None:
    keys = {k: _env_value(REPO_ENV, k) for k in
            ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID")}
    missing = [k for k, v in keys.items() if not v]
    trading = all(keys[k] for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"))
    if trading and not missing:
        record("PASS", "repo .env", "alpaca + slack set")
    elif trading:
        record("WARN", "repo .env", f"slack incomplete: {missing}")
    else:
        record("FAIL", "repo .env", f"trading keys missing: {missing}")


def check_ta_env() -> None:
    if not _env_value(TA_ENV, "NVIDIA_API_KEY"):
        record("FAIL", "NVIDIA_API_KEY", "not set in TradingAgents/.env - research cannot run")
    else:
        record("PASS", "NVIDIA_API_KEY", "set")
    if not _env_value(TA_ENV, "FRED_API_KEY"):
        record("WARN", "FRED_API_KEY", "not set - macro analyst flies blind")
    else:
        record("PASS", "FRED_API_KEY", "set")


def check_junction() -> None:
    target = (ROOT / "outputs" / "paper")
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "AI-cio-outputs" / "paper"
    if target.is_dir() and target.resolve() == local.resolve():
        record("PASS", "outputs junction", f"-> {local}")
    elif target.is_dir():
        record("WARN", "outputs location", f"{target} is inside the sync boundary")
    else:
        record("FAIL", "outputs junction", f"{target} missing")


def check_openai_quota() -> None:
    """If an OpenAI key is set, a 1-token call proves it has credits.

    A key with no credits (429 credit_balance_exhausted) passes every other
    check and then crashes the deep tier mid-run; this catches it preflight.
    """
    key = _env_value(ROOT.parent / "TradingAgents" / ".env", "OPENAI_API_KEY")
    if not key:
        record("WARN", "OPENAI_API_KEY", "not set - judgment tier stays on NIM")
        return
    try:
        import json
        import urllib.request

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps({"model": "gpt-4o-mini", "messages":
                             [{"role": "user", "content": "hi"}],
                             "max_tokens": 1}).encode(),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            json.loads(resp.read() or b"{}")
        record("PASS", "OPENAI_API_KEY", "key works and has credits")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if "credit_balance_exhausted" in body or "insufficient_quota" in body:
            record("WARN", "OPENAI_API_KEY",
                   "key valid but NO CREDITS (add billing; tier stays on NIM)")
        else:
            record("FAIL", "OPENAI_API_KEY", f"HTTP {exc.code}: {body[:100]}")
    except Exception as exc:  # noqa: BLE001
        record("WARN", "OPENAI_API_KEY", f"probe failed: {type(exc).__name__}")


def check_ta_venv() -> None:
    if not TA_PY.exists():
        record("FAIL", "TradingAgents venv", f"{TA_PY} missing")
        return
    proc = subprocess.run([str(TA_PY), "-c", "import tradingagents"],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        record("PASS", "TradingAgents venv", "imports tradingagents")
    else:
        record("FAIL", "TradingAgents venv",
               f"import failed: {(proc.stderr or '?').strip()[-120:]}")


def check_broker() -> None:
    try:
        from robinhood_tools.alpaca_paper import (AlpacaPaperBackend,
                                                   AlpacaPaperHttpTransport)
        from robinhood_tools.settings import load_env

        env = {**load_env(str(REPO_ENV)), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        accounts = backend.list_accounts()
        record("PASS", "alpaca paper", f"account {accounts[0].masked_account_number}")
    except Exception as exc:  # noqa: BLE001 - report, never crash
        record("FAIL", "alpaca paper", f"{type(exc).__name__}: {exc}")


def check_office() -> None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8766/api/status", timeout=5) as r:
            json.loads(r.read() or b"{}")
        record("PASS", "office viewer", "answering on :8766")
    except Exception as exc:  # noqa: BLE001
        record("WARN", "office viewer", f"not answering ({type(exc).__name__})")


def run_tests() -> None:
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                          capture_output=True, text=True)
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
    if proc.returncode == 0:
        record("PASS", "unit tests", tail[0] if tail else "ok")
    else:
        record("FAIL", "unit tests", tail[0] if tail else "failures")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Desk preflight readiness checks.")
    p.add_argument("--full", action="store_true", help="also run the unit test suite")
    args = p.parse_args(argv)

    check_tasks()
    check_repo_env()
    check_ta_env()
    check_openai_quota()
    check_junction()
    check_ta_venv()
    check_broker()
    check_office()
    if args.full:
        run_tests()

    fails = 0
    for status, name, detail in results:
        print(f"[{status:4}] {name:22} {detail}")
        if status == "FAIL":
            fails += 1
    print(f"\npreflight: {len(results) - fails} ok/warn, {fails} FAIL")
    return fails


if __name__ == "__main__":
    raise SystemExit(main())
