#!/usr/bin/env python3
"""Append-only per-cycle archive of intraday setup artifacts.

The live scout overwrites ``outputs/daytrade/<date>.json`` each cycle, so
earlier point-in-time setups (including 10:45 short breakdowns) are lost. This
module writes an immutable snapshot per cycle; it never changes live routing.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def archive_setup(source: Path, root: Path | None = None) -> Path | None:
    """Copy the current setup artifact into a timestamped archive folder."""
    source = Path(source)
    if not source.is_file():
        return None
    date = source.stem
    repo = Path(root) if root else source.resolve().parents[2]
    stamp = datetime.now(ET).strftime("%H%M%S")
    destination = repo / "outputs/daytrade/archive" / date / f"{stamp}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        stamp = datetime.now(ET).strftime("%H%M%S%f")
        destination = destination.with_name(f"{stamp}.json")
    shutil.copy2(source, destination)
    return destination if destination.is_file() else None


def latest_archived(root: Path, date: str) -> Path | None:
    folder = Path(root) / "outputs/daytrade/archive" / date
    if not folder.is_dir():
        return None
    matches = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def archived_records(root: Path, date: str) -> list[dict]:
    """All point-in-time setups from archived cycles, newest first."""
    folder = Path(root) / "outputs/daytrade/archive" / date
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        for setup in doc.get("setups") or []:
            if not isinstance(setup, dict):
                continue
            rows.append({**setup, "archive": path.name,
                         "generated_at": doc.get("generated_at")})
    return rows
