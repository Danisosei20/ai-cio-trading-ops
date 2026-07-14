#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

PATTERNS = [
    re.compile(r"(?i)(password|access_token|api_key|cookie)[ \t]*[=:][ \t]*[^\s$][^\s]*"),
    re.compile(r"(?i)account_number[ \t]*[=:][ \t]*['\"]?\d{8,12}"),
]
SKIP = {".env", ".git", "outputs", "__pycache__", ".venv"}


def contains_potential_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in PATTERNS)


def main() -> int:
    findings = []
    for path in Path.cwd().rglob("*"):
        if not path.is_file() or any(part in SKIP for part in path.parts):
            continue
        if path.suffix not in {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".example"} and path.name != ".env.example":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if contains_potential_secret(text):
            findings.append(str(path))
    if findings:
        print("potential secrets found: " + ", ".join(sorted(findings)))
        return 2
    print("secret scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
