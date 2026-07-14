#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import shutil
import subprocess
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install the independent AI CIO missed-run watchdog.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--memory", default="~/.codex/automations/ai-cio-daily-review/memory.md")
    parser.add_argument(
        "--test-alert", action="store_true",
        help="After installation, send one clearly labeled non-trading health-route test.",
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    values = load_env(root / args.env_file)
    token = values.get("SLACK_BOT_TOKEN", "")
    channel = values.get("HEALTH_SLACK_CHANNEL_ID", "")
    timezone_name = values.get("TIMEZONE", "America/New_York")
    if not token or not channel.startswith(("C", "G")):
        raise SystemExit("SLACK_BOT_TOKEN and a fixed HEALTH_SLACK_CHANNEL_ID are required in .env.")

    home = Path.home()
    install_dir = home / "Library" / "Application Support" / "OpenAI-AICIO-Watchdog"
    launch_agents = home / "Library" / "LaunchAgents"
    install_dir.mkdir(parents=True, exist_ok=True)
    launch_agents.mkdir(parents=True, exist_ok=True)
    script_path = install_dir / "standalone_watchdog.py"
    shutil.copy2(root / "scripts" / "standalone_watchdog.py", script_path)
    script_path.chmod(0o700)

    service = "openai.ai-cio-watchdog.slack"
    account = "ai-cio-watchdog"
    subprocess.run(
        ["/usr/bin/security", "add-generic-password", "-U", "-s", service, "-a", account, "-w", token],
        check=True, capture_output=True, text=True,
    )

    memory = str(Path(args.memory).expanduser())
    state_file = str(install_dir / "state.json")
    stdout = str(install_dir / "watchdog.log")
    stderr = str(install_dir / "watchdog-error.log")
    escaped = {name: html.escape(value) for name, value in {
        "script": str(script_path), "memory": memory, "state": state_file,
        "channel": channel, "timezone": timezone_name, "stdout": stdout, "stderr": stderr,
    }.items()}
    plist = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.openai.ai-cio-watchdog</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/python3</string><string>{escaped["script"]}</string>
    <string>--memory</string><string>{escaped["memory"]}</string>
    <string>--state-file</string><string>{escaped["state"]}</string>
    <string>--health-channel</string><string>{escaped["channel"]}</string>
    <string>--timezone</string><string>{escaped["timezone"]}</string>
    <string>--scheduled-time</string><string>09:45</string>
    <string>--grace-minutes</string><string>15</string>
  </array>
  <key>StartCalendarInterval</key><array>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>5</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>5</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>5</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>5</integer></dict>
    <dict><key>Weekday</key><integer>6</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>5</integer></dict>
  </array>
  <key>StandardOutPath</key><string>{escaped["stdout"]}</string>
  <key>StandardErrorPath</key><string>{escaped["stderr"]}</string>
</dict></plist>
'''
    plist_path = launch_agents / "com.openai.ai-cio-watchdog.plist"
    plist_path.write_text(plist, encoding="utf-8")
    uid = str(subprocess.run(["/usr/bin/id", "-u"], check=True, capture_output=True, text=True).stdout.strip())
    subprocess.run(["/bin/launchctl", "bootout", f"gui/{uid}/com.openai.ai-cio-watchdog"], check=False)
    subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], check=True)
    print(f"Installed {plist_path}")
    if args.test_alert:
        result = subprocess.run(
            [
                "/usr/bin/python3", str(script_path),
                "--memory", memory,
                "--state-file", state_file,
                "--health-channel", channel,
                "--timezone", timezone_name,
                "--scheduled-time", "09:45",
                "--grace-minutes", "15",
                "--test-alert",
            ],
            check=False, capture_output=True, text=True,
        )
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.returncode != 0:
            if result.stderr.strip():
                print(result.stderr.strip())
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
