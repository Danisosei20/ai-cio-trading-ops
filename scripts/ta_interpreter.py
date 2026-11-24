#!/usr/bin/env python3
"""Fail-closed discovery of the TradingAgents-capable Python interpreter.

Research stages depend on the sibling ``TradingAgents`` virtual environment.
Historically the orchestrator silently fell back to ``sys.executable`` (the
desk venv) when that environment was missing, producing artifact errors such as
``No module named 'tradingagents'`` instead of a clear gate failure.

One resolver is shared by the orchestrator and preflight so they always agree
on which interpreter is "the TradingAgents runtime". A missing or unusable
runtime raises :class:`RuntimeError`; it is never replaced with an interpreter
that cannot import the dependency.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ENV_VAR = "TRADINGAGENTS_VENV_PYTHON"


def default_ta_python(root: Path) -> Path:
    """The documented sibling TradingAgents venv interpreter."""
    venv = Path(root).parent / "TradingAgents" / ".venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ta_python_candidates(root: Path) -> list[Path]:
    """Verified candidate order: explicit config, current interpreter, sibling.

    The scheduler launchers invoke the orchestrator *with* the TradingAgents
    venv, so ``sys.executable`` is often already the correct runtime. It is only
    a candidate after verification; a desk interpreter without the dependency
    is never accepted as a silent fallback.
    """
    configured = os.environ.get(ENV_VAR, "").strip()
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = Path(root) / candidate
        return [candidate]
    return [Path(sys.executable), default_ta_python(root)]


def resolve_ta_python(root: Path) -> Path:
    """The preferred interpreter (explicit config, else the running one)."""
    return ta_python_candidates(root)[0]


def verify_ta_python(path: Path) -> tuple[bool, str]:
    """Return ``(ok, detail)`` after checking the interpreter can import the dep."""
    path = Path(path)
    if not path.exists():
        return False, f"TradingAgents interpreter missing: {path}"
    try:
        proc = subprocess.run([str(path), "-c", "import tradingagents"],
                              capture_output=True, text=True, timeout=60)
    except OSError as exc:
        return False, f"cannot launch {path}: {exc}"
    if proc.returncode != 0:
        lines = [ln.strip() for ln in (proc.stderr or proc.stdout or "").splitlines()
                 if ln.strip()]
        detail = lines[-1] if lines else "unknown import error"
        return False, f"{path} cannot import tradingagents: {detail}"
    return True, f"{path} imports tradingagents"


def find_tradingagents_python(root: Path) -> tuple[str | None, list[str]]:
    """Return the first verified interpreter and all verification errors."""
    errors: list[str] = []
    for path in ta_python_candidates(root):
        ok, detail = verify_ta_python(path)
        if ok:
            return str(path), errors
        errors.append(detail)
    return None, errors


def require_tradingagents_python(root: Path) -> str:
    """Resolve and verify the TA interpreter, or fail closed with a clear error."""
    path, errors = find_tradingagents_python(root)
    if path is None:
        raise RuntimeError(
            "TradingAgents runtime unavailable: " + "; ".join(errors) +
            f". Set {ENV_VAR} to a Python that imports tradingagents; "
            "research stages fail closed (no silent fallback)."
        )
    return path
