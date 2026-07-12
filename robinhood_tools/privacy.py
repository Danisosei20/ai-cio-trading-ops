from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path


SENSITIVE_NAMES = {".env", "cio.db", "approval_routes.json"}
SECRET_PATTERN = re.compile(r"(?i)(token|password|cookie|authorization|account_number)\s*[=:]\s*\S+")


def create_safe_support_bundle(root: str | Path, destination: str | Path) -> Path:
    root = Path(root).resolve()
    destination = Path(destination)
    allowed = ["README.md", ".env.example", "pyproject.toml", "Makefile", "mcp.json"]
    allowed += [str(path.relative_to(root)) for path in root.glob("docs/*.md")]
    allowed += [str(path.relative_to(root)) for path in root.glob("config/*.example.json")]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(set(allowed)):
            path = root / relative
            if not path.exists() or path.name in SENSITIVE_NAMES:
                continue
            content = path.read_text(encoding="utf-8")
            content = SECRET_PATTERN.sub(r"\1=REDACTED", content)
            archive.writestr(relative, content)
        archive.writestr("bundle-manifest.json", json.dumps({"safe_bundle": True, "excluded": sorted(SENSITIVE_NAMES)}, indent=2))
    return destination
