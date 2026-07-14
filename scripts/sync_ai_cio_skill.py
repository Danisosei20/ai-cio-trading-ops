#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path


SKILL_NAME = "ai-cio-portfolio-manager"
SYNCED_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/approval_routing.md",
    "references/investment_policy.md",
    "references/journal_schema.md",
    "references/market_signals.md",
    "references/robinhood_workflow.md",
    "references/skill_learning.md",
    "scripts/update_journal.py",
)


def default_installed_path() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return codex_home / "skills" / SKILL_NAME


def default_repository_path() -> Path:
    return Path(__file__).resolve().parents[1] / "skills" / SKILL_NAME


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_safe_source(root: Path, relative_path: str) -> Path:
    path = root / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"Required skill file is missing: {path}")
    if path.is_symlink():
        raise ValueError(f"Refusing to sync symlinked skill file: {path}")
    return path


def drift(installed: Path, repository: Path) -> list[str]:
    differences: list[str] = []
    for relative_path in SYNCED_FILES:
        installed_file = installed / relative_path
        repository_file = repository / relative_path
        if not installed_file.is_file():
            differences.append(f"installed missing: {relative_path}")
        if not repository_file.is_file():
            differences.append(f"repository missing: {relative_path}")
        if installed_file.is_file() and repository_file.is_file() and digest(installed_file) != digest(repository_file):
            differences.append(f"content differs: {relative_path}")
    return differences


def sync(source: Path, destination: Path) -> None:
    source_files = [(relative_path, require_safe_source(source, relative_path)) for relative_path in SYNCED_FILES]
    for relative_path, source_file in source_files:
        destination_file = destination / relative_path
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        if destination_file.is_symlink():
            raise ValueError(f"Refusing to replace symlinked skill file: {destination_file}")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination_file.parent, prefix=".skill-sync-", delete=False,
            ) as temporary:
                temporary.write(source_file.read_bytes())
                temporary_path = Path(temporary.name)
            temporary_path.chmod(source_file.stat().st_mode & 0o777)
            os.replace(temporary_path, destination_file)
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keep the installed and repository AI CIO skill copies in sync.")
    parser.add_argument("mode", choices=("check", "from-installed", "to-installed"))
    parser.add_argument("--installed", type=Path, default=default_installed_path())
    parser.add_argument("--repository", type=Path, default=default_repository_path())
    args = parser.parse_args(argv)

    installed = args.installed.expanduser().resolve()
    repository = args.repository.expanduser().resolve()
    if installed == repository:
        parser.error("Installed and repository skill paths must be different.")

    if args.mode == "from-installed":
        sync(installed, repository)
    elif args.mode == "to-installed":
        sync(repository, installed)

    differences = drift(installed, repository)
    print(json.dumps({
        "state": "in_sync" if not differences else "drift",
        "mode": args.mode,
        "files": len(SYNCED_FILES),
        "differences": differences,
    }, sort_keys=True))
    return 0 if not differences else 1


if __name__ == "__main__":
    raise SystemExit(main())
