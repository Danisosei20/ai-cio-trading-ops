#!/usr/bin/env python3
"""risk_off_short_challenger — SHADOW-ONLY research strategy.

Evaluates pre-registered short R outcomes across regimes with walk-forward /
out-of-sample separation and realistic costs. It never promotes itself, never
changes RISK_OFF, never authorizes or submits an order, and never reports
shadow outcomes as realized profit.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MIN_OOS_SAMPLES = 30


def evaluate(rows: list[dict], *, cost_bps: float = 5.0,
             borrow_bps_per_day: float = 2.5,
             min_oos_samples: int = MIN_OOS_SAMPLES) -> dict:
    """Evaluate R-multiple rows split into in-sample and out-of-sample.

    Each row must carry ``r_multiple`` (pre-cost, from a point-in-time replay),
    ``regime``, ``date`` and optional ``borrow_days``. Rows without these are
    ignored and counted, never imputed.
    """
    valid, invalid = [], 0
    for row in rows or []:
        if not isinstance(row, dict):
            invalid += 1
            continue
        try:
            r = float(row["r_multiple"])
            date = str(row["date"])
            regime = str(row.get("regime", "UNKNOWN"))
            days = max(1, int(row.get("borrow_days", 1)))
        except (KeyError, TypeError, ValueError):
            invalid += 1
            continue
        net = r - cost_bps / 100.0 - borrow_bps_per_day * days / 10000.0
        valid.append({"date": date, "regime": regime, "r": r, "net_r": net})

    valid.sort(key=lambda x: x["date"])
    split = max(1, int(len(valid) * 0.6))
    ins, oos = valid[:split], valid[split:]

    def stats(samples):
        if not samples:
            return {"samples": 0, "expectancy_r": None, "win_rate": None,
                    "drawdown_r": None, "sharpe_like": None}
        values = [x["net_r"] for x in samples]
        wins = [v for v in values if v > 0]
        equity, peak, max_dd = 0.0, 0.0, 0.0
        for v in values:
            equity += v
            peak = max(peak, equity)
            max_dd = min(max_dd, equity - peak)
        sd = (sum((v - mean(values)) ** 2 for v in values) / len(values)) ** 0.5
        return {
            "samples": len(values),
            "expectancy_r": round(mean(values), 4),
            "win_rate": round(len(wins) / len(values), 4),
            "drawdown_r": round(max_dd, 4),
            "sharpe_like": round(mean(values) / sd, 4) if sd else None,
        }

    by_regime = {}
    for item in valid:
        by_regime.setdefault(item["regime"], []).append(item)

    sufficient = len(oos) >= min_oos_samples
    ins_stats, oos_stats = stats(ins), stats(oos)
    return {
        "strategy": "risk_off_short_challenger",
        "shadow_only": True,
        "orders_submitted": 0,
        "enforcement_enabled": False,
        "entry_permission": "none - RISK_OFF policy unchanged; shadow research only",
        "generated_at": datetime.now(ET).isoformat(),
        "method": {
            "walk_forward": "chronological 60/40 in-sample / out-of-sample split",
            "costs_bps_round_trip": cost_bps,
            "borrow_bps_per_day": borrow_bps_per_day,
            "samples_ignored": invalid,
        },
        "in_sample": ins_stats,
        "out_of_sample": oos_stats,
        "by_regime": {key: stats(value) for key, value in sorted(by_regime.items())},
        "evidence_sufficient": sufficient,
        "decision": ("insufficient out-of-sample evidence; no promotion request" if not sufficient
                     else "research signal only; promotion requires ADR/policy update, "
                          "out-of-sample robustness review and explicit owner approval"),
    }


def write_report(result: dict, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    return result
