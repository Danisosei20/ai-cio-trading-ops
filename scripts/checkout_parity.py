#!/usr/bin/env python3
"""Read-only development/operational checkout parity comparison.

Only file names, hashes and git state are read. No secret content is opened,
copied or written anywhere. This module never modifies either checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

# Exact allowlist of reviewed operational components. Secrets, owner staged
# files, untracked work and virtual environments are deliberately excluded.
ALLOWLIST = (
    "scripts/intraday_loop.bat", "scripts/auto_trade_weekday.bat",
    "scripts/flatten_1545.bat", "scripts/overnight_enter.bat",
    "scripts/overnight_exit.bat", "scripts/options_watch.bat",
    "scripts/news_daily.bat", "scripts/score_shadow.bat",
    "scripts/watchdog_windows.bat", "scripts/weekly_review.bat",
    "scripts/daytrade_1030.bat", "scripts/office_server.bat",
    "scripts/workflow_runner.py", "scripts/flatten_verified.py",
    "scripts/flatten_daytrades.py", "scripts/intraday_loop.py",
    "scripts/overnight_pilot.py", "scripts/operational_reliability.py",
    "scripts/daily_reliability_rehearsal.py",
    "scripts/champion_equiv_benchmark.py", "scripts/agent_office.py",
    "robinhood_tools/workflow_manifest.py", "web/agent-office/trading.html",
    "docs/specs/intraday-setups/v1.0.0/SPEC.md",
    "docs/adr/0001-deterministic-gate-ai-research-only.md",
    "docs/adr/0002-execution-authorization-protocol.md",
)

MATCH = "MATCH"
DEPLOY = "DEPLOYMENT_REQUIRED"
CONFLICT = "CONFLICT"
NOT_VERIFIED = "NOT_VERIFIED"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _locally_modified(root: Path, rel: str) -> bool:
    in_repo = any((root / name).exists() for name in (".git",)) or \
        any((parent / ".git").exists() for parent in root.parents)
    if not in_repo:
        return False  # isolated non-repo fixture, nothing tracked
    for args in (["git", "diff", "--quiet", "--", rel],
                 ["git", "diff", "--cached", "--quiet", "--", rel]):
        try:
            p = subprocess.run(args, cwd=root, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return True  # git unavailable -> never overwrite
        if p.returncode == 1:
            return True
        if p.returncode != 0:
            return True  # git error -> fail closed
    return False


def compare(source: Path, destination: Path, *, allowlist=ALLOWLIST) -> dict:
    source, destination = Path(source), Path(destination)
    rows = []
    for rel in allowlist:
        src, dst = source / rel, destination / rel
        if not src.is_file():
            rows.append({"component": rel, "classification": NOT_VERIFIED,
                         "reason": "source missing"})
            continue
        src_hash = sha256(src)
        if not dst.is_file():
            rows.append({"component": rel, "source_sha256": src_hash,
                         "destination_sha256": None,
                         "classification": DEPLOY,
                         "reason": "destination missing"})
            continue
        dst_hash = sha256(dst)
        modified = _locally_modified(destination, rel)
        if modified:
            classification, reason = CONFLICT, "destination has local owner changes"
        elif src_hash == dst_hash:
            classification, reason = MATCH, "hash identical"
        else:
            classification, reason = DEPLOY, "hash differs; reviewed source is newer"
        rows.append({"component": rel, "source_sha256": src_hash,
                     "destination_sha256": dst_hash,
                     "last_modified_utc": datetime.fromtimestamp(
                         dst.stat().st_mtime).isoformat(),
                     "classification": classification, "reason": reason})
    counts = {}
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    return {"track": "checkout_parity", "generated_at": datetime.now().isoformat(),
            "source": str(source), "destination": str(destination),
            "counts": counts, "rows": rows,
            "secret_policy": "names and hashes only; no secret files opened or copied",
            "scheduler_policy": "no registered task modified"}


def render_markdown(report: dict) -> str:
    lines = ["# Checkout parity", "", f"Source: `{report['source']}`",
             f"Destination: `{report['destination']}`", "",
             "| Component | Classification | Reason | Source SHA-256 | Destination SHA-256 |",
             "|---|---|---|---|---|"]
    for row in report["rows"]:
        lines.append("| {} | {} | {} | {} | {} |".format(
            row["component"], row["classification"], row["reason"],
            row.get("source_sha256", "")[:16],
            (row.get("destination_sha256") or "")[:16]))
    lines += ["", "No secret content is included in this report.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--destination", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None)
    args = ap.parse_args(argv)
    report = compare(args.source, args.destination)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
