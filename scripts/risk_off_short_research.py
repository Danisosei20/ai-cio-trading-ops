#!/usr/bin/env python3
"""SHADOW-ONLY research track for short setups under a RISK_OFF regime.

This is research tooling, not an execution path. It consumes the output of
``scripts/daytrade_backtest.py`` (the real-pipeline replay) and emits a report
that is explicitly marked shadow-only. It never imports an order-placement
module, never enables authorization enforcement, and never submits orders.

A bearish research result is NOT permission to short. Any rule that would
allow entries under the desk-wide RISK_OFF restriction needs a documented
policy update, tests, and explicit owner approval.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def shadow_report(backtest: dict, *, note: str = "") -> dict:
    """Summarise RISK_OFF short evidence from a daytrade_backtest report."""
    totals = backtest.get("totals") or {}
    regimes = totals.get("regimes") or {}
    risk_off = regimes.get("RISK_OFF") or {"trades": 0, "net_pnl": 0.0}
    per_ticker = []
    for row in backtest.get("per_ticker") or []:
        ro = (row.get("regimes") or {}).get("RISK_OFF") or {}
        if ro.get("trades"):
            per_ticker.append({
                "ticker": row.get("ticker"),
                "risk_off_trades": ro["trades"],
                "risk_off_net_pnl": round(float(ro["net_pnl"]), 2),
            })
    evidence = "insufficient" if risk_off.get("trades", 0) < 20 else "provisional"
    return {
        "generated_at": datetime.now(ET).isoformat(),
        "track": "risk_off_short_shadow",
        "shadow_only": True,
        "orders_submitted": 0,
        "entry_permission": "none - policy unchanged; RISK_OFF still blocks new risk desk-wide",
        "enforcement_enabled": False,
        "backtest_window_days": backtest.get("window_days"),
        "backtest_tickers": backtest.get("tickers") or [],
        "backtest_caveats": backtest.get("caveats") or [],
        "risk_off_short_samples": risk_off.get("trades", 0),
        "risk_off_short_net_pnl": round(float(risk_off.get("net_pnl", 0.0)), 2),
        "per_ticker_risk_off": per_ticker,
        "evidence_sufficiency": evidence,
        "decision": ("no policy change proposed - collect more RISK_OFF samples "
                     "in shadow mode before any review request"),
        "note": note,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bt-json", required=True,
                    help="output of scripts/daytrade_backtest.py --out")
    ap.add_argument("--out", required=True, help="shadow report destination")
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)
    backtest = json.loads(Path(args.bt_json).read_text(encoding="utf-8"))
    report = shadow_report(backtest, note=args.note)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"shadow report -> {out}; RISK_OFF samples: "
          f"{report['risk_off_short_samples']}; orders: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
