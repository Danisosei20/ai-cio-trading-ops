#!/usr/bin/env python3
"""Rollback-capable deployment helper for the reviewed launcher allowlist.

Default is DRY RUN. Applying requires both ``--apply`` and
``--owner-authorized``. Nothing is copied outside the exact allowlist, no
secrets are read or copied, and no Task Scheduler definition is changed.
Batch files are never replaced during the active session window because
cmd.exe seeks running scripts by byte offset.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.checkout_parity import (  # noqa: E402
    CONFLICT, DEPLOY, NOT_VERIFIED, ALLOWLIST, compare, sha256)

ET = ZoneInfo("America/New_York")
ACTIVE_START = time(9, 45)
ACTIVE_END = time(16, 5)


def active_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(ET)
    return (now.weekday() < 5 and ACTIVE_START <= now.time() <= ACTIVE_END)


def running_ai_tasks() -> list[str]:
    if os.name != "nt":
        return []  # no Windows Task Scheduler on this host
    script = "Get-ScheduledTask | Where-Object { $_.State -eq 'Running' -and $_.TaskName -like 'AI-*' } | Select-Object -ExpandProperty TaskName"
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-Command", script], capture_output=True, text=True, timeout=30)
        return [line.strip() for line in p.stdout.splitlines() if line.strip()]
    except Exception:  # noqa: BLE001 - unknown state fails closed
        return ["<scheduler query unavailable>"]


CERTIFIED_SOURCE_COMMIT = "1c5dc8c"


def source_commit(source: Path) -> str:
    try:
        p = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=source,
                           capture_output=True, text=True, timeout=60)
        return p.stdout.strip()
    except Exception:  # noqa: BLE001 - unknown source fails closed
        return ""


def plan(source: Path, destination: Path, *, now: datetime | None = None,
         require_all_sources: bool = True) -> dict:
    report = compare(source, destination, allowlist=ALLOWLIST)
    actions = []
    for row in report["rows"]:
        if row["classification"] == CONFLICT:
            actions.append({"component": row["component"], "action": "BLOCKED",
                            "reason": "owner local changes"})
        elif row["classification"] == DEPLOY:
            actions.append({"component": row["component"], "action": "COPY",
                            "source_sha256": row.get("source_sha256")})
        elif row["classification"] == NOT_VERIFIED:
            actions.append({"component": row["component"],
                            "action": ("BLOCKED" if require_all_sources
                                       else "SKIP_UNAVAILABLE"),
                            "reason": "source unavailable"})
        else:
            actions.append({"component": row["component"], "action": "SKIP",
                            "reason": "already matching"})
    return {
        "track": "hardened_launcher_deployment", "mode": "dry_run",
        "source": str(source), "destination": str(destination),
        "planned_at": (now or datetime.now(ET)).isoformat(),
        "owner_authorized": False, "applied": False,
        "active_window": active_window(now),
        "running_tasks": running_ai_tasks(),
        "actions": actions,
        "secrets_touched": False, "scheduler_modified": False,
        "rollback": "Use --rollback <backup-manifest.json> with --owner-authorized",
    }


def apply(source: Path, destination: Path, *, backup_root: Path,
          owner_authorized: bool = False, require_all_sources: bool = True,
          expected_commit: str | None = None,
          now: datetime | None = None) -> dict:
    if not owner_authorized:
        return {"error": "apply requires owner authorization", "applied": False}
    if expected_commit and source_commit(source) != expected_commit:
        return {"error": "source commit does not match certified commit",
                "source_commit": source_commit(source),
                "expected_commit": expected_commit, "applied": False}
    result = plan(source, destination, now=now,
                  require_all_sources=require_all_sources)
    blocked = [a["component"] for a in result["actions"]
               if a["action"] == "BLOCKED"]
    if blocked:
        result["error"] = f"blocked files: {blocked}"
        result["applied"] = False
        return result
    if active_window(now):
        result["error"] = "refuse to replace batch files during 09:45-16:05 ET"
        return result
    if result["running_tasks"]:
        result["error"] = "refuse to deploy while AI tasks are running"
        return result
    backup = backup_root / (datetime.now(ET).strftime("%Y%m%d-%H%M%S")
                            + "-" + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True, exist_ok=False)
    manifest = {
        "track": "launcher_deployment_backup", "created_at": datetime.now(ET).isoformat(),
        "source": str(source), "destination": str(destination), "backup_dir": str(backup),
        "files": [], "partial": True,
        "scheduler_export": "not modified - no task XML restore needed",
    }
    manifest_path = backup / "backup-manifest.json"

    def write_manifest() -> None:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    write_manifest()  # exists before the first replacement
    applied = []
    for action in result["actions"]:
        if action["action"] != "COPY":
            continue
        rel = action["component"]
        src, dst = source / rel, destination / rel
        expected = sha256(src)
        if sha256(src) != action.get("source_sha256", expected):
            result["error"] = f"source hash changed during deployment: {rel}"
            write_manifest()
            return result
        created = not dst.exists()
        if not created:
            saved = backup / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, saved)
            manifest["files"].append({"component": rel, "created": False,
                                       "backup_sha256": sha256(saved),
                                       "pre_deploy_sha256": sha256(dst)})
        else:
            manifest["files"].append({"component": rel, "created": True,
                                       "backup_sha256": None,
                                       "pre_deploy_sha256": None})
        write_manifest()  # persist the backup record before the risky replace
        temp = dst.with_name(dst.name + "." + uuid.uuid4().hex + ".tmp")
        shutil.copyfile(src, temp)
        os.replace(temp, dst)
        if sha256(dst) != expected:
            result["error"] = f"post-deploy hash mismatch: {rel}"
            write_manifest()
            return result
        applied.append(rel)
        write_manifest()
    manifest["partial"] = False
    write_manifest()
    result.update({"mode": "applied", "applied": True, "files_applied": applied,
                   "backup_manifest": str(manifest_path)})
    return result


def rollback(manifest_path: Path) -> dict:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    destination = Path(manifest["destination"])
    backup = Path(manifest["backup_dir"])
    if active_window():
        return {"error": "refuse rollback during 09:45-16:05 ET"}
    running = running_ai_tasks()
    if running:
        return {"error": "refuse rollback while AI tasks are running",
                "running_tasks": running}
    restored, removed = [], []
    for item in manifest["files"]:
        rel = item["component"]
        dst = destination / rel
        if item.get("created"):
            if dst.exists():
                dst.unlink()
                removed.append(rel)
            continue
        src = backup / rel
        if not src.is_file() or sha256(src) != item["backup_sha256"]:
            return {"restored": restored, "removed": removed,
                    "error": f"backup hash mismatch: {rel}"}
        temp = dst.with_name(dst.name + "." + uuid.uuid4().hex + ".tmp")
        shutil.copyfile(src, temp)
        os.replace(temp, dst)
        if sha256(dst) != item["backup_sha256"]:
            return {"restored": restored, "removed": removed,
                    "error": f"rollback hash mismatch: {rel}"}
        restored.append(rel)
    return {"track": "launcher_rollback", "restored": restored, "removed": removed,
            "scheduler_modified": False, "secrets_touched": False}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--destination", type=Path, required=False)
    ap.add_argument("--backup-root", type=Path, default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--owner-authorized", action="store_true")
    ap.add_argument("--rollback", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.rollback:
        if not args.owner_authorized:
            print("REFUSED: rollback requires --owner-authorized")
            return 2
        result = rollback(args.rollback)
    else:
        if args.destination is None:
            print("--destination is required")
            return 2
        result = plan(args.source, args.destination)
        if args.apply:
            if not args.owner_authorized:
                print("REFUSED: apply requires --owner-authorized")
                return 2
            result = apply(args.source, args.destination,
                           backup_root=args.backup_root or Path(tempfile.gettempdir()),
                           owner_authorized=args.owner_authorized,
                           expected_commit=CERTIFIED_SOURCE_COMMIT)
    text = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text[:2000])
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
