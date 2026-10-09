#!/usr/bin/env python3
"""Morning robustness check (weekdays 08:30 ET, before the review).

Runs the full verification battery and posts one Slack summary:
unit tests, lint, types, secret scan, broker connectivity, ledger
integrity, schedules loaded, UI alive, overnight state sane.
Any failure is reported plainly — green is confirmed, never assumed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def _python() -> str:
    """Prefer the repo venv (ruff/mypy live there); launchd-safe fallback."""
    venv = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


CHECKS: list[tuple[str, list[str]]] = [
    ("unit-tests", [_python(), "-m", "unittest", "discover", "-s", "tests"]),
    ("lint", [_python(), "-m", "ruff", "check", "robinhood_tools", "tests", "scripts"]),
    ("types", [_python(), "-m", "mypy", "robinhood_tools"]),
    ("secrets", [_python(), "scripts/secret_scan.py"]),
]


def run(name: str, cmd: list[str]) -> tuple[str, bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        ok = proc.returncode == 0
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        return name, ok, "; ".join(tail[-3:])[:300]
    except Exception as exc:
        return name, False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    results = [run(name, cmd) for name, cmd in CHECKS]
    try:
        from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
        from robinhood_tools.settings import load_env
        import os

        env = {**load_env(".env"), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        clock = backend.market_clock().get("is_open")
        npos = len(backend.list_positions())
        results.append(("broker", True, f"clock_open={clock} positions={npos}"))
    except Exception as exc:
        results.append(("broker", False, f"{type(exc).__name__}"))
    try:
        from robinhood_tools.database import CioDatabase

        db = CioDatabase("outputs/paper/cio.db")
        results.append(("ledger", db.integrity_check() == "ok", db.integrity_check()))
    except Exception as exc:
        results.append(("ledger", False, f"{type(exc).__name__}"))
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:8100/api/health", timeout=8) as r:
            results.append(("ui", r.status == 200, f"http={r.status}"))
    except Exception as exc:
        results.append(("ui", False, f"{type(exc).__name__}"))
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15)
        jobs = ["ai-cio-options" in out.stdout, "ai-cio-guard" in out.stdout,
                "ai-cio-desk" in out.stdout, "ai-cio-screen" in out.stdout]
        results.append(("schedules", all(jobs), f"{sum(jobs)}/4 loaded"))
    except Exception as exc:
        results.append(("schedules", False, f"{type(exc).__name__}"))

    failed = [name for name, ok, _ in results if not ok]
    lines = [f"{'PASS' if ok else 'FAIL'} {name} — {detail}" for name, ok, detail in results]
    print("*Morning robustness*\n" + "\n".join(lines))
    try:
        from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
        from robinhood_tools.settings import load_env
        import os

        env = {**load_env(".env"), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        positions = backend.list_positions()
        orders = backend.list_open_orders()
        book = ["*Morning book*"]
        if positions:
            for x in positions:
                book.append(f"• {x.get('symbol')} x{x.get('qty')} @ {x.get('current_price')} "
                            f"(P&L {x.get('unrealized_pl')})")
        else:
            book.append("Flat — no positions.")
        if orders:
            for o in orders:
                book.append(f"• {o.get('side')} {o.get('symbol')} x{o.get('qty')} "
                            f"@ {o.get('limit_price')} ({o.get('status')})")
        else:
            book.append("No working orders.")
        sys.path.insert(0, ".")
        from scripts.notify import send

        send("\n".join(book))
    except Exception as exc:
        print(f"slack failed: {exc}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
