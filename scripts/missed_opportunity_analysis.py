#!/usr/bin/env python3
"""Point-in-time analysis of rejected or unexecuted trading opportunities.

READ ONLY. This module never imports a broker/order module, never places an
order, and never uses post-signal information to reconstruct an entry. If a
signal did not carry an entry, stop and target at the time it was generated,
its hypothetical outcome is ``not_determinable`` - a later price move is not
permission to call it a missed winner.

Optional ``--post-bars`` data is used *only* to report observed subsequent
movement with explicit timestamps; it never feeds entry/stop/target.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

VIX_BLOCK = "vix_policy"
REGIME_BLOCK = "regime_policy"
NO_SHORT_PATH = "no_short_entry_path"
TECHNICAL = "technical_failure"
OTHER = "other_structural"

RISK_CATEGORIES = {VIX_BLOCK, REGIME_BLOCK, NO_SHORT_PATH, OTHER}


def classify_rejection(reason: str) -> str:
    """Map a recorded rejection string to a stable, testable category."""
    text = (reason or "").strip().lower()
    if not text:
        return OTHER
    if text.startswith(("error", "module", "traceback")) or any(
            token in text for token in ("modulenotfound", "traceback", "exception",
                                        "timeout", "connection error", "unavailable",
                                        "judge failed", "judge error", "scout error")):
        return TECHNICAL
    if "vix" in text:
        return VIX_BLOCK
    if "risk_off" in text or "regime" in text or "circuit breaker" in text:
        return REGIME_BLOCK
    if "no position held" in text or "bearish" in text:
        # Bearish analyst bias is an exit-only signal in the equity path; the
        # absence of a short entry branch is structural, not a market veto.
        return NO_SHORT_PATH
    if "odds" in text or "reward" in text or "zone" in text or "borrow" in text:
        return OTHER
    return OTHER


def analyst_bias(decision: str) -> str:
    """Bias of the research verdict. NOT an order direction."""
    value = (decision or "").strip().lower()
    if value in {"buy", "overweight"}:
        return "long"
    if value in {"sell", "underweight"}:
        return "bearish_research"
    return "neutral"


def is_automatic_short(decision: str) -> bool:
    """A bearish/Underweight verdict must never become a short automatically."""
    return False


def _load_json(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return doc if isinstance(doc, dict) else None


def load_signals(root: Path, date: str) -> tuple[list[dict], list[str]]:
    """Load per-symbol research artifacts for one session date."""
    base = root / "outputs" / "paper" / "tradingagents"
    rows: list[dict] = []
    gaps: list[str] = []
    if not base.is_dir():
        return rows, [f"missing research artifact root: {base}"]
    for auto in sorted(base.glob(f"*/{date}/auto.json")):
        doc = _load_json(auto)
        if doc is None:
            gaps.append(f"unreadable research artifact: {auto}")
            continue
        symbol = str(doc.get("ticker") or auto.parent.parent.name).upper()
        reason = str(doc.get("reason") or "")
        rows.append({
            "symbol": symbol,
            "analyst_bias": analyst_bias(str(doc.get("decision") or "")),
            "strategy": str(doc.get("desk_verdict") or doc.get("desk_outcome") or ""),
            "source": str(auto.relative_to(root)).replace("\\", "/"),
            "reason": reason,
            "category": classify_rejection(reason),
            "action": str(doc.get("action") or ""),
            "entry": doc.get("entry"),
            "stop": doc.get("stop"),
            "target": doc.get("target"),
            "price": doc.get("price"),
            "price_as_of": doc.get("price_as_of"),
            "price_source": doc.get("price_source"),
        })
    return rows, gaps


def load_setups(root: Path, date: str) -> dict[str, dict]:
    """Point-in-time intraday setups (price/VWAP/OR) keyed by symbol."""
    path = root / "outputs" / "daytrade" / f"{date}.json"
    doc = _load_json(path)
    if not doc:
        return {}
    setups = doc.get("setups")
    if not isinstance(setups, list):
        return {}
    return {str(s.get("ticker", "")).upper(): s for s in setups
            if isinstance(s, dict)}


def load_shadow_reasons(root: Path, date: str) -> dict[str, str]:
    """Read-only shadow-record reasons from the desk SQLite ledger."""
    db = root / "outputs" / "paper" / "cio.db"
    if not db.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        rows = con.execute(
            "SELECT symbol, payload FROM shadow_equity_recommendations "
            "WHERE observed_at LIKE ?", (f"{date}%",)).fetchall()
        con.close()
    except sqlite3.Error:
        return {}
    out: dict[str, str] = {}
    for symbol, payload in rows:
        try:
            doc = json.loads(payload)
        except (TypeError, ValueError):
            continue
        out[str(symbol).upper()] = str(doc.get("blocked_reason") or "")
    return out


def load_technical_failures(root: Path, date: str) -> list[str]:
    """Research/scanner errors dated today - technical, never a market veto."""
    log = root / "outputs" / "intraday.log"
    if not log.exists():
        return []
    failures: list[str] = []
    try:
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith(date):
                continue
            lowered = line.lower()
            if any(k in lowered for k in ("judge failed", "judge error", "scout error",
                                          "module not found", "modulenotfound")):
                failures.append(line.strip()[:240])
    except OSError:
        return []
    return failures


def _subsequent(post_bars: dict, symbol: str, as_of: str) -> dict:
    """Observed movement AFTER the signal. Never used to set entry/stop/target."""
    bars = post_bars.get(symbol) or []
    future = []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        ts = str(bar.get("ts") or "")
        if ts and ts > as_of and bar.get("close") is not None:
            future.append((ts, float(bar["close"])))
    if not future:
        return {"status": "unavailable", "reason": "no timestamped post-signal bars"}
    future.sort()
    first = float(future[0][1])
    last = float(future[-1][1])
    return {
        "status": "ok",
        "first_post_signal_close": round(first, 4),
        "last_post_signal_close": round(last, 4),
        "change_pct": round((last - first) / first * 100, 3) if first else 0.0,
        "note": "observational only; not an entry or a trade result",
    }


def hypothetical(entry, stop, target, post: dict, cost_bps: float) -> dict:
    """A hypothetical R outcome ONLY from levels recorded at signal time."""
    try:
        e, s, t = float(entry), float(stop), float(target)
    except (TypeError, ValueError):
        return {"status": "not_determinable", "reason": "entry/stop/target absent at signal time"}
    if e <= 0 or s <= 0 or t <= 0 or e == s:
        return {"status": "not_determinable", "reason": "invalid recorded levels"}
    if post.get("status") != "ok":
        return {"status": "not_determinable", "reason": "no valid post-signal path data"}
    last = float(post["last_post_signal_close"])
    risk = abs(e - s)
    direction = 1 if t > e else -1  # +1 long, -1 short
    hit_target = (last - e) * direction >= abs(t - e)
    hit_stop = (last - e) * direction <= -risk
    if hit_target and hit_stop:
        return {"status": "not_determinable", "reason": "intrabar path ambiguous"}
    if not hit_target and not hit_stop:
        return {"status": "not_determinable", "reason": "neither target nor stop touched"}
    gross_r = direction * (t - e) / risk if hit_target else -1.0
    net_r = round(gross_r - (2 * cost_bps / 10000) * (e / risk), 3)
    return {
        "status": "hypothetical",
        "r_multiple_before_cost": round(gross_r, 3),
        "r_multiple_after_cost": net_r,
        "cost_bps_round_trip": 2 * cost_bps,
        "note": "simulation, not a broker fill and not realized profit",
    }


def analyze(root: Path, date: str, *, as_of: str, session_complete: bool,
            post_bars: dict | None, cost_bps: float) -> dict:
    signals, gaps = load_signals(root, date)
    setups = load_setups(root, date)
    shadow = load_shadow_reasons(root, date)
    failures = load_technical_failures(root, date)
    post_bars = post_bars or {}

    rows: list[dict] = []
    for sig in signals:
        symbol = sig["symbol"]
        setup = setups.get(symbol, {})
        # Point-in-time fields only from artifacts created at signal time.
        price = sig["price"] or setup.get("price")
        price_as_of = sig["price_as_of"] or (setups and setup.get("generated_at"))
        reason = shadow.get(symbol) or sig["reason"]
        category = classify_rejection(reason) if reason else sig["category"]
        post = _subsequent(post_bars, symbol, as_of)
        outcome = hypothetical(sig["entry"], sig["stop"], sig["target"], post, cost_bps)
        if category == TECHNICAL:
            final = "technical_failure"
        elif outcome["status"] == "hypothetical":
            final = "hypothetical_outcome"
        elif category in RISK_CATEGORIES:
            final = "valid_policy_rejection"
        else:
            final = "insufficient_evidence"
        quality = "partial"
        if sig["source"] and price is not None and reason:
            quality = "point_in_time"
        elif not sig["source"]:
            quality = "missing"
        rows.append({
            "symbol": symbol,
            "analyst_bias": sig["analyst_bias"],
            "automatic_short": is_automatic_short(sig["analyst_bias"]),
            "strategy": sig["strategy"],
            "decision_source": sig["source"],
            "decision_as_of": str(price_as_of or ""),
            "market_regime": "RISK_OFF" if "risk_off" in reason.lower() else "not_recorded",
            "entry_eligible": False,
            "rejection_reason": reason,
            "rejection_category": category,
            "point_in_time_price": price,
            "price_source": sig["price_source"] or "intraday setup artifact",
            "vwap": setup.get("vwap"),
            "opening_range": ({"high": setup.get("or_high"), "low": setup.get("or_low")}
                              if setup else None),
            "setup": setup.get("setup"),
            "proposed_entry": sig["entry"],
            "proposed_stop": sig["stop"],
            "proposed_target": sig["target"],
            "planned_rr": None,
            "subsequent_movement": post,
            "hypothetical_outcome": outcome,
            "transaction_cost_bps_round_trip": 2 * cost_bps,
            "data_quality": quality,
            "research_confidence": "not_recorded",
            "final_classification": final,
        })

    counts = {
        "candidates": len(rows),
        "long_bias": sum(r["analyst_bias"] == "long" for r in rows),
        "bearish_research": sum(r["analyst_bias"] == "bearish_research" for r in rows),
        "valid_policy_rejection": sum(r["final_classification"] == "valid_policy_rejection" for r in rows),
        "technical_failure": sum(r["final_classification"] == "technical_failure" for r in rows),
        "insufficient_evidence": sum(r["final_classification"] == "insufficient_evidence" for r in rows),
        "hypothetical_outcome": sum(r["final_classification"] == "hypothetical_outcome" for r in rows),
    }
    return {
        "track": "missed_opportunity_analysis",
        "generated_at": datetime.now(ET).isoformat(),
        "session_date": date,
        "as_of": as_of,
        "session_complete": bool(session_complete),
        "shadow_only": True,
        "orders_submitted": 0,
        "entry_permission": "none - analysis only",
        "enforcement_enabled": False,
        "order modules imported": False,
        "sources": ["outputs/paper/tradingagents/*/<date>/auto.json",
                    f"outputs/daytrade/{date}.json",
                    "outputs/paper/cio.db (shadow_equity_recommendations, read-only)",
                    "outputs/intraday.log"],
        "counts": counts,
        "candidates": rows,
        "technical_failures": failures,
        "data_gaps": gaps + [
            "no entry/stop/target were generated for rejected candidates",
            "no timestamped post-signal bar series was supplied by default",
            "VIX observation has no retained as-of timestamp in the source artifacts",
        ],
        "note": ("A bearish/Underweight research verdict is not a short order. "
                 "Post-signal price movement is observation, not a missed winner. "
                 "No result here is a broker fill or realized profit."),
    }


def markdown(report: dict) -> str:
    lines = [
        f"# Missed-opportunity analysis — {report['session_date']}",
        "",
        f"As of {report['as_of']} ET · session complete: {report['session_complete']}",
        "SHADOW ONLY · orders submitted: 0 · entry permission: none",
        "",
        "| Symbol | Bias | Why blocked | Hypothetical | Evidence |",
        "|---|---|---|---|---|",
    ]
    for r in report["candidates"]:
        lines.append(
            f"| {r['symbol']} | {r['analyst_bias']} | {r['rejection_category']} | "
            f"{r['hypothetical_outcome']['status']} | {r['data_quality']} |")
    lines += ["", "## Data gaps", ""]
    lines += [f"- {g}" for g in report["data_gaps"]]
    lines += ["", report["note"], ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--as-of", default="")
    ap.add_argument("--session-complete", action="store_true")
    ap.add_argument("--post-bars", type=Path, default=None,
                    help="optional JSON of timestamped closes, observational only")
    ap.add_argument("--cost-bps", type=float, default=5.0)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None)
    args = ap.parse_args(argv)
    as_of = args.as_of or datetime.now(ET).isoformat()
    post = None
    if args.post_bars:
        loaded = _load_json(args.post_bars)
        post = loaded if isinstance(loaded, dict) else {}
    report = analyze(args.root, args.date, as_of=as_of,
                     session_complete=args.session_complete,
                     post_bars=post, cost_bps=args.cost_bps)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(markdown(report), encoding="utf-8")
    if not args.out and not args.md:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"analyzed {report['counts']['candidates']} candidates; orders: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
