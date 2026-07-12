from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .errors import PolicyViolation

VARIABLE = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


def load_env(path: str | Path = ".env") -> dict[str, str]:
    """Load simple KEY=VALUE settings without overwriting process environment."""
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for line_number, raw in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PolicyViolation(f"Invalid .env line {line_number}: expected KEY=VALUE.")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise PolicyViolation(f"Invalid .env variable name {key!r} on line {line_number}.")
        values[key] = value.strip().strip('"').strip("'")
    return values


def load_config(path: str | Path, env_path: str | Path = ".env") -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    variables = {**load_env(env_path), **os.environ}
    return _resolve(data, variables)


def _resolve(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, variables) for item in value]
    if not isinstance(value, str):
        return value
    match = VARIABLE.fullmatch(value)
    if not match:
        return value
    name = match.group(1)
    if name not in variables or variables[name] == "":
        raise PolicyViolation(f"Required personal variable {name} is missing; set it in .env.")
    raw = variables[name]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
