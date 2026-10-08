#!/usr/bin/env python3
"""Send a Slack notification to the fixed channel. Notify, never authorize.

  python3 scripts/notify.py "NVDA stopped at 230.31 (-2.86%)"
  python3 scripts/notify.py --watch   # diff state, notify on changes only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STATE_FILE = Path("outputs/paper/notify_state.json")


def send(text: str) -> dict:
    import os

    from robinhood_tools.settings import load_env
    from robinhood_tools.slack_web_api import SlackWebApiNotifier

    env = {**load_env(".env"), **os.environ}
    notifier = SlackWebApiNotifier.from_values(env)
    return notifier.send_approval(channel_id=env.get("SLACK_CHANNEL_ID", ""),
                                  message=text)


def snapshot() -> dict:
    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
    from robinhood_tools.settings import load_env
    import os

    env = {**load_env(".env"), **os.environ}
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    positions = {str(p.get("symbol")): {"qty": str(p.get("qty")),
                                        "mark": str(p.get("current_price"))}
                 for p in backend.list_positions()}
    orders = {o.get("id"): {"symbol": o.get("symbol"), "side": o.get("side"),
                            "status": o.get("status")}
              for o in backend.list_open_orders()}
    guard = {}
    gf = Path("outputs/paper/guard.json")
    if gf.exists():
        try:
            guard = {p["symbol"]: p["signal"] for p in
                     json.loads(gf.read_text()).get("positions", [])}
        except Exception:
            pass
    return {"positions": positions, "orders": orders, "guard": guard}


def watch() -> int:
    previous = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    current = snapshot()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(current, indent=2))
    if not previous:
        print("baseline recorded; no alert on first run.")
        return 0
    notes = []
    for sym, pos in current["positions"].items():
        old = previous.get("positions", {}).get(sym)
        if old is None:
            notes.append(f"Opened {sym} x{pos['qty']} @ ~{pos['mark']}")
        elif old["qty"] != pos["qty"]:
            notes.append(f"{sym} qty {old['qty']} -> {pos['qty']}")
    for sym in previous.get("positions", {}):
        if sym not in current["positions"]:
            notes.append(f"Flat {sym} (position closed)")
    for oid, o in current["orders"].items():
        if oid not in previous.get("orders", {}):
            notes.append(f"Order {o['side']} {o['symbol']} {o['status']}")
    for sym, sig in current["guard"].items():
        if sig in {"STOP", "TARGET"} and previous.get("guard", {}).get(sym) != sig:
            notes.append(f"Guard {sig}: {sym}")
    if notes:
        send("Desk alert:\n- " + "\n- ".join(notes))
        print("notified:", len(notes))
    else:
        print("no changes.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Desk Slack notifications.")
    ap.add_argument("message", nargs="?", default="")
    ap.add_argument("--watch", action="store_true")
    a = ap.parse_args(argv)
    if a.watch:
        return watch()
    if not a.message:
        print("usage: notify.py \"message\" | notify.py --watch", file=sys.stderr)
        return 2
    result = send(a.message)
    print("sent", result.get("message_ts", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
