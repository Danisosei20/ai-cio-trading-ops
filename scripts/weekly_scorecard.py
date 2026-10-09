#!/usr/bin/env python3
"""Weekly scorecard: realized P&L, win rate, strategy splits, gate stats.

Sources: broker closed orders + fills (authoritative), paper ledger
(approvals/debates), guard states. Posts one Slack summary with --send.

  python3 scripts/weekly_scorecard.py --dry-run
  python3 scripts/weekly_scorecard.py --send
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUT = Path("outputs/scorecard/latest.json")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Weekly trading scorecard.")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--send", action="store_true")
    p.add_argument("--config", default="config/approval_routes.json")
    p.add_argument("--env-file", default=".env")
    return p.parse_args(argv)


def score_round_trips(fills: list[dict]) -> dict:
    """Pure scoring from fill dicts {symbol, side, qty, price, time}.

    Matches sells against earlier buys FIFO per symbol. Unmatched sells
    (no recorded buy) count proceeds only, flagged as unmatched.
    """
    lots: dict[str, list] = {}
    realized = Decimal("0")
    wins = losses = 0
    matched_pnl: list[Decimal] = []
    unmatched = 0
    for f in sorted(fills, key=lambda x: x["time"]):
        try:
            qty = Decimal(str(f["qty"]))
            price = Decimal(str(f["price"]))
        except (InvalidOperation, KeyError, TypeError):
            continue
        book = lots.setdefault(f["symbol"], [])
        if f["side"] == "buy":
            book.append([qty, price])
        else:
            need, cost = qty, Decimal("0")
            while need > 0 and book:
                lot_qty, lot_px = book[0]
                take = min(need, lot_qty)
                cost += take * lot_px
                lot_qty -= take
                need -= take
                if lot_qty <= 0:
                    book.pop(0)
                else:
                    book[0][0] = lot_qty
            if need > 0:
                unmatched += 1
                continue
            pnl = qty * price - cost
            realized += pnl
            matched_pnl.append(pnl)
            if pnl > 0:
                wins += 1
            else:
                losses += 1
    avg_win = sum(p for p in matched_pnl if p > 0) / wins if wins else Decimal("0")
    avg_loss = sum(p for p in matched_pnl if p <= 0) / losses if losses else Decimal("0")
    gross_w = sum(p for p in matched_pnl if p > 0)
    gross_l = sum(-p for p in matched_pnl if p <= 0)
    return {"realized": str(realized), "trades": wins + losses, "wins": wins,
            "losses": losses,
            "win_rate": round(wins / (wins + losses), 3) if wins + losses else 0.0,
            "avg_win": str(round(avg_win, 2)), "avg_loss": str(round(avg_loss, 2)),
            "profit_factor": str(round(gross_w / gross_l, 2)) if gross_l > 0 else None,
            "unmatched_sells": unmatched}


def format_card(score: dict, debates: dict, week: str) -> str:
    lines = [f"*Weekly scorecard — {week}*"]
    lines.append(f"Realized *${score['realized']}* · {score['trades']} closed "
                 f"({score['wins']}W/{score['losses']}L, {score['win_rate']:.0%} win rate)")
    lines.append(f"Avg win ${score['avg_win']} / avg loss ${score['avg_loss']} · "
                 f"profit factor {score['profit_factor'] or 'n/a'}")
    if score["unmatched_sells"]:
        lines.append(f"_({score['unmatched_sells']} sells without recorded buys — "
                     f"pre-ledger exits, proceeds uncounted)_")
    if debates:
        lines.append("Desk: " + ", ".join(f"{k} {v}" for k, v in sorted(debates.items())))
    return "\n".join(lines)


def main(argv=None) -> int:
    args = parse_args(argv)
    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
    from robinhood_tools.settings import load_env
    import os

    env = {**load_env(args.env_file), **os.environ}
    backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
    since = (date.today() - timedelta(days=args.days)).isoformat()
    orders = backend.transport.request("GET", "/v2/orders?status=closed&limit=500&direction=desc")
    fills = []
    for o in orders:
        if str(o.get("submitted_at", ""))[:10] < since:
            continue
        oid = o.get("id")
        try:
            acts = backend.transport.request(
                "GET", "/v2/account/activities?activity_types=FILL&page_size=100&direction=desc")
        except Exception:
            acts = []
        for a in acts:
            if str(a.get("order_id")) != oid:
                continue
            fills.append({"symbol": o.get("symbol"), "side": o.get("side"),
                          "qty": a.get("qty"), "price": a.get("price"),
                          "time": a.get("transaction_time", "")})
    score = score_round_trips(fills)

    debates: dict[str, int] = {}
    root = Path("outputs/paper/tradingagents")
    if root.exists():
        for auto in root.glob("*/*/auto.json"):
            try:
                d = json.loads(auto.read_text())
                if d.get("date", "") >= since:
                    key = f"{d.get('decision', '?')}->{d.get('action', '?')}"
                    debates[key] = debates.get(key, 0) + 1
            except Exception:
                continue
    week = f"{since} to {date.today().isoformat()}"
    card = format_card(score, debates, week)
    print(card)
    payload = {"week": week, "score": score, "debates": debates}
    if not args.dry_run:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=2))
    if args.send:
        sys.path.insert(0, ".")
        from scripts.notify import send

        result = send(card)
        print("sent", result.get("message_ts", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
