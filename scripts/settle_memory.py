#!/usr/bin/env python3
"""Settle the desk's memory log: outcomes + reflections, so the debate learns.

The TradingAgents graph already stores every final decision (pending) in its
memory log and already injects settled entries -- same-ticker decisions plus
cross-ticker reflections -- into later runs. What was never wired: a caller
that SETTLES pending entries once their holding window has traded. This job:

  - pins the log at outputs/learning/trading_memory.md (migrating the
    default-path log on first run, so nothing is orphaned),
  - fetches real returns vs each ticker's benchmark and writes an LLM
    reflection per settled decision,
  - the next 09:45 debate then reads those lessons automatically.

Runs in the 16:30 batch (after market close, via score_shadow.bat).
Usage:
  ../TradingAgents/.venv/bin/python scripts/settle_memory.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PINNED = ROOT / "outputs" / "learning" / "trading_memory.md"
DEFAULT = Path(os.path.expanduser("~")) / ".tradingagents" / "memory" / "trading_memory.md"
PROVIDER = "nvidia"
MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
REFLECTION_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"  # lessons shape future decisions


def _load_llm_env() -> None:
    dotenv = ROOT.parent / "TradingAgents" / ".env"
    if not dotenv.exists():
        return
    try:
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value:
                os.environ.setdefault(key, value)
    except OSError:
        pass


def _migrate() -> None:
    PINNED.parent.mkdir(parents=True, exist_ok=True)
    if not PINNED.exists() and DEFAULT.exists():
        shutil.copyfile(DEFAULT, PINNED)
        print(f"settle_memory: migrated {DEFAULT} -> {PINNED}")
    elif not PINNED.exists():
        PINNED.write_text("", encoding="utf-8")


def _config() -> dict:
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = PROVIDER
    config["quick_think_llm"] = MODEL
    config["deep_think_llm"] = REFLECTION_MODEL
    config["memory_log_path"] = str(PINNED)
    return config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Settle the desk's memory log.")
    p.add_argument("--dry-run", action="store_true",
                   help="show pending entries; settle nothing")
    args = p.parse_args(argv)

    _load_llm_env()
    _migrate()
    config = _config()

    from tradingagents.dataflows.config import run_config
    from tradingagents.llm_clients import create_tier_client
    from tradingagents.memory import TradingMemoryLog, settlement
    from tradingagents.memory.reflection import Reflector

    log = TradingMemoryLog(config)
    pending = log.get_pending_entries()
    print(f"settle_memory: {len(pending)} pending decision(s) at {PINNED}")
    for entry in pending:
        print(f"  - [{entry.get('date')} | {entry.get('ticker')} | {entry.get('rating')}]")
    if args.dry_run:
        return 0

    llm = create_tier_client(config, "deep").get_llm()
    reflector = Reflector(llm)
    with run_config(config):
        result = settlement.settle_all_pending(log, reflector, config)
    payload = asdict(result) if is_dataclass(result) else result
    print(f"settle_memory: settled -> {payload}")
    remaining = log.get_pending_entries()
    print(f"settle_memory: {len(remaining)} still pending (windows not traded yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
