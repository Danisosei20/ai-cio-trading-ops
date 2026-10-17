"""Small, atomic status feed for the read-only fleet office."""
import json
import os
import time
from pathlib import Path

NAMES = ("screen", "research", "news", "verify", "report")
STATES = ("idle", "queued", "working", "complete", "blocked", "skipped", "unknown")


def outcome(result):
    if result.get("error") or result.get("returncode", 0) != 0 or result.get("ok") is False:
        return "blocked"
    if "runs" in result:
        if not result["runs"]:
            return "skipped"
        if any(r.get("returncode") != 0 for r in result["runs"]):
            return "blocked"
    return "complete"


def publish(root, run_id, tasks, active=None, finished=False):
    path = Path(root) / "outputs/office-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(run_id=run_id, tasks=tasks, active=active,
                finished=finished, updated=time.time(), pid=os.getpid())
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data))
    temporary.replace(path)


def snapshot(root):
    root = Path(root)
    try:
        data = json.loads((root / "outputs/office-state.json").read_text())
        if not isinstance(data.get("tasks"), dict):
            raise ValueError("invalid tasks")
        # Running status is only credible while its process and heartbeat live.
        alive = False
        try:
            pid = int(data.get("pid", 0))
            if pid > 0:
                os.kill(pid, 0)
                alive = True
        except (OSError, ValueError, TypeError):
            pass
        live = alive and time.time() - data.get("updated", 0) < 15 and not data.get("finished")
        tasks = {name: data["tasks"].get(name, "idle") for name in NAMES}
        tasks = {name: value if value in STATES else "unknown" for name, value in tasks.items()}
        if not live:
            tasks = {n: "unknown" if s == "working" else s for n, s in tasks.items()}
        return dict(mode="live" if live else "idle", tasks=tasks,
                    run_id=data.get("run_id"), updated=data.get("updated"),
                    note="Task pipeline active" if live else "Last run • workers off duty")
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    # Existing fleet artifacts are historical, never presented as live work.
    try:
        data = json.loads((root / "outputs/fleet-summary.json").read_text())
        results = data.get("results", {})
        return dict(mode="idle", tasks={n: outcome(results[n]) if n in results else "idle" for n in NAMES},
                    run_id=data.get("date"), updated=None, note="Historical results • waiting for next run")
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(mode="idle", tasks={n: "idle" for n in NAMES}, run_id=None,
                    updated=None, note="No fleet results yet • try the demo")
