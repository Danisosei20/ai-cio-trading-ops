#!/usr/bin/env python3
"""Point-in-time walk-forward evaluation for SPEC v1.0.0 long/short rules.

Evaluation-only. Entry uses the NEXT bar open; features never use future bars;
outcomes are resolved from later bars with target-first vs stop-first priority.
Cost scenarios are reported but never presented as realized results. Stubbed
gates, unresolved paths and insufficient samples produce NOT_VERIFIED.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from robinhood_tools import pit_dataset
from robinhood_tools.bar_validation import long_reclaim, short_breakdown

ET = ZoneInfo("America/New_York")
SPEC_VERSION = "intraday-setups/v1.0.0"
MIN_TRADES_FOR_CLAIM = 30
MAX_UNRESOLVED_RATIO = 0.2
HORIZON_BARS = 12
COMMISSIONS = (0.0, 0.005, 0.01)
SLIPPAGE_BPS = (0.0, 2.5, 5.0, 10.0)
BORROW_BPS_DAY = (0.0, 2.5, 5.0, 10.0)


def assign_splits(session_dates: list[str]) -> dict[str, list[str]]:
    dates = sorted(set(session_dates))
    if len(dates) < 5:
        return {"dev": dates, "validation": [], "final": []}
    n_dev = max(3, int(len(dates) * 0.6))
    n_val = max(1, int(len(dates) * 0.2))
    return {"dev": dates[:n_dev],
            "validation": dates[n_dev:n_dev + n_val],
            "final": dates[n_dev + n_val:]}


def _rule(setup: str, price: float, vwap: float, or_high: float, or_low: float):
    if setup == "long_reclaim":
        return long_reclaim(price, vwap=vwap, or_high=or_high, or_low=or_low), "long"
    return short_breakdown(price, vwap=vwap, or_high=or_high, or_low=or_low), "short"


def _features(bars: list[dict], index: int) -> tuple[float, float, float, float]:
    window = bars[:index + 1]
    closes = [float(b.get("close", 0)) for b in window]
    vols = [float(b.get("volume", 0)) for b in window]
    vwap = (sum((float(b.get("high", 0)) + float(b.get("low", 0)) + c) / 3 * v
                for b, c, v in zip(window, closes, vols)) / sum(vols)
            if sum(vols) else 0.0)
    first6 = window[:6]
    return (closes[-1], vwap,
            max(float(b.get("high", 0)) for b in first6),
            min(float(b.get("low", 0)) for b in first6))


def _outcome(bars: list[dict], entry_index: int, direction: str,
             entry: float, stop: float, target: float) -> str:
    """Target-first vs stop-first on future bars; no same-bar entry lookahead."""
    for bar in bars[entry_index + 1:entry_index + 1 + HORIZON_BARS]:
        high, low = float(bar.get("high", 0)), float(bar.get("low", 0))
        if direction == "long":
            if stop > 0 and low <= stop:
                return "stop"
            if high >= target:
                return "target"
        else:
            if low <= target:
                return "target"
            if high >= stop:
                return "stop"
    return "unresolved"


def _metrics(pnls: list[float]) -> dict:
    if not pnls:
        return {"trades": 0, "win_rate": None, "expectancy": None,
                "profit_factor": None, "max_drawdown": None}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    peak, drawdown = 0.0, 0.0
    for p in pnls:
        peak = max(peak, peak + p)
        drawdown = min(drawdown, (peak + p) / peak - 1) if peak else 0.0
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    return {
        "trades": len(pnls),
        "win_rate": round(len(wins) / len(pnls), 4),
        "expectancy": round(sum(pnls) / len(pnls), 6),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else None,
        "max_drawdown": round(drawdown, 6),
    }


def evaluate(root: Path, dataset_id: str, *, calendar_dates: list[str] | None = None,
             unseal_final: bool = False) -> dict:
    audit = pit_dataset.audit(root, dataset_id, calendar_dates=calendar_dates)
    entries = pit_dataset.load_entries(root, dataset_id)
    splits = assign_splits([e["session_date"] for e in entries])
    # The final holdout is never used for rule selection or metrics.
    # Eligibility is derived from dates, never from unsigned metadata.
    def split_for(date: str) -> str:
        if date in splits["dev"]:
            return "dev"
        if date in splits["validation"]:
            return "validation"
        return "final"

    eligible = [e for e in entries if audit["accepted_for_replay"] and
                split_for(e["session_date"]) != "final"]
    reasons = []
    for e in entries:
        if e.get("split") != split_for(e["session_date"]):
            reasons.append(f"split mismatch: {e['session_date']}")
    if not audit["accepted_for_replay"]:
        reasons.extend(audit["problems"] or ["dataset audit rejected"])
    if not entries:
        reasons.append("no dataset entries")
    if not unseal_final and splits["final"]:
        reasons.append("final holdout sealed")

    results = {}
    for setup in ("long_reclaim", "short_breakdown"):
        trades = []
        for entry in eligible:
            doc = json.loads(Path(entry["snapshot_path"]).read_text(encoding="utf-8"))
            bars = doc.get("bars") or []
            for i in range(12, len(bars) - 1):
                price, vwap, or_high, or_low = _features(bars, i)
                (accepted, _), direction = _rule(setup, price, vwap, or_high, or_low)
                if not accepted:
                    continue
                entry_price = float(bars[i + 1].get("open", 0))
                stop = or_low if direction == "long" else or_high
                risk = abs(entry_price - stop)
                if risk <= 0:
                    continue
                target = entry_price + 2 * risk * (1 if direction == "long" else -1)
                outcome = _outcome(bars, i + 1, direction, entry_price, stop, target)
                trades.append({"outcome": outcome, "direction": direction})
        unique_signals = len(trades)
        unresolved = sum(1 for t in trades if t["outcome"] == "unresolved")
        resolved = [t for t in trades if t["outcome"] != "unresolved"]
        scenario_pnls = []
        for trade in resolved:
            r_multiple = 2.0 if trade["outcome"] == "target" else -1.0
            price = 100.0  # notional basis only; no fabricated market quotes
            for commission in COMMISSIONS:
                for slip in SLIPPAGE_BPS:
                    for borrow in BORROW_BPS_DAY:
                        cost = commission * 2 + 2 * price * slip / 10000
                        if trade["direction"] == "short":
                            cost += price * borrow / 10000
                        scenario_pnls.append(r_multiple - cost)
        metrics = _metrics(scenario_pnls)
        metrics.update({"unique_signals": unique_signals,
                        "resolved_signals": len(resolved),
                        "unresolved_signals": unresolved,
                        "metrics_label": "SCENARIO_ONLY"})
        if (unresolved / unique_signals if unique_signals else 1.0) > MAX_UNRESOLVED_RATIO:
            reasons.append(f"{setup}: unresolved outcomes exceed limit")
        if unique_signals < MIN_TRADES_FOR_CLAIM:
            reasons.append(f"{setup}: {unique_signals} signals below minimum "
                           f"{MIN_TRADES_FOR_CLAIM}")
        results[setup] = {
            "verdict": "EVALUATED" if not reasons else "NOT_VERIFIED",
            "metrics": metrics,
            "cost_scenarios": len(COMMISSIONS) * len(SLIPPAGE_BPS) * len(BORROW_BPS_DAY),
            "borrow_evidence": "NOT VERIFIED - no broker snapshot",
            "entry_basis": "next bar open",
            "outcome_basis": "target-first vs stop-first over future bars",
        }
    return {
        "track": "walk_forward_evaluation", "generated_at": datetime.now(ET).isoformat(),
        "dataset_id": dataset_id, "spec_version": SPEC_VERSION,
        "splits": splits, "unsealed_final": unseal_final,
        "cost_scenarios": {"commissions": COMMISSIONS, "slippage_bps": SLIPPAGE_BPS,
                           "borrow_bps_day": BORROW_BPS_DAY},
        "results": results, "reasons": sorted(set(reasons)),
        "verdict": "NOT_VERIFIED" if reasons else "EVALUATED",
        "promotion": "none", "entry_permission": "none - evaluation only",
        "note": "Scenario figures are not realized P/L; no live eligibility is granted.",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--dataset-id", required=True)
    ap.add_argument("--unseal-final", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    report = evaluate(args.root, args.dataset_id, unseal_final=args.unseal_final)
    text = json.dumps(report, indent=2, default=str) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(report["verdict"], report["reasons"][:3])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
