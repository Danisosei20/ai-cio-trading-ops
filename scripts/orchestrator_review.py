"""Review CLI for the paper-desk orchestrator.

Read-only by default: ``--latest`` prints the latest pointer and ``--prove``
prints a run's proof matrix. ``--override`` records an audited override for a
warn agent in a ``review`` run and can never clear a ``halt``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.desk_orchestrator import prove, read_latest, record_override

_ET = ZoneInfo("America/New_York")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repo root (defaults to this repo)")
    parser.add_argument("--latest", action="store_true", help="print the latest run pointer")
    parser.add_argument("--prove", metavar="RUN_ID", help="print the proof matrix for a run")
    parser.add_argument("--override", metavar="RUN_ID", help="record an override for a run")
    parser.add_argument("--agent", help="agent name whose warn report is being overridden")
    parser.add_argument("--reason", help="owner's reason for the override")
    args = parser.parse_args(argv)

    if args.latest:
        print(json.dumps(read_latest(args.root), indent=2))
    elif args.prove:
        print(prove(args.root, args.prove))
    elif args.override:
        result = record_override(
            args.root,
            run_id=args.override,
            agent=args.agent or "",
            reason=args.reason or "",
            now_iso=datetime.now(_ET).isoformat(),
        )
        print(result)
    else:
        parser.error("choose --latest, --prove RUN_ID, or --override RUN_ID")


if __name__ == "__main__":
    main()
