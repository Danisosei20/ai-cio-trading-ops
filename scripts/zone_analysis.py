#!/usr/bin/env python3
"""Volume profile + supply/demand zones for the desk's day trading.

Owner directive 2026-10-27: trade like a day trader, taking POC (point of
control) and supply/demand into account. Per ticker, per session:

  - VWAP (intraday volume-weighted price)
  - volume-at-price profile -> POC (highest-volume price), VAH/VAL
    (the 70% value area around it)
  - demand nodes (heavy-volume buckets below POC) and supply nodes
    (heavy-volume buckets above POC)
  - prior session high/low/close as reference levels

Outputs outputs/zones/<date>/<TICKER>.json plus a confluenced verdict:

  LONG confirmed unless price is pinned at a supply node with no room
    overhead, or structure is broken well below prior VAL/VWAP.
  SHORT mirrored. verdict values: "long_ok" | "long_blocked" |
  "short_ok" | "short_blocked" | "neutral", each with reasons.

This layer CONFIRMS or BLOCKS - it never decides the trade (the LLM
judgment tier does). Thin early sessions (market just opened) fall back
to the prior session's profile and are flagged "developing".

Usage:
  python scripts/zone_analysis.py --tickers SPY,NVDA
  python scripts/zone_analysis.py --tickers SPY --date 2026-10-26
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

ET = ZoneInfo("America/New_York")
ZONE_DIR = ROOT / "outputs" / "zones"
NODE_MULTIPLIER = 1.5   # a bucket >= 1.5x mean volume is a supply/demand node
EMA_FAST = 9            # fast EMA period (5m bars) for intraday trend
EMA_SLOW = 21           # slow EMA period (5m bars) for intraday trend
PIN_DISTANCE = 0.0025   # within 0.25% of a node = "at" the node
BROKEN_DISTANCE = 0.015 # > 1.5% below prior VAL/VWAP = broken structure


def _bucket_size(price: float) -> float:
    for threshold, size in ((20, 0.05), (100, 0.10), (500, 0.25),
                            (2000, 0.50), (20000, 2.0)):
        if price < threshold:
            return size
    return 10.0


def _ema(closes: list[float], period: int) -> float | None:
    """Exponential moving average of the close series (last value)."""
    if not closes or len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return round(ema, 4)


def _session_bars(bars, session: str):
    """Rows for one session date (YYYY-MM-DD)."""
    return bars[[str(i)[:10] == session for i in bars.index]]


def _session_profile(session_bars) -> dict:
    """Volume-at-price profile for one session (typical-price bucketing)."""
    if session_bars is None or len(session_bars) == 0:
        return {}
    mid = float(session_bars["Close"].iloc[-1])
    size = _bucket_size(mid)
    vol_by_bucket: dict[float, float] = {}
    total_vol = 0.0
    vwap_num = 0.0
    for _, row in session_bars.iterrows():
        typical = (float(row["High"]) + float(row["Low"]) + float(row["Close"])) / 3
        volume = float(row["Volume"])
        if volume <= 0:
            continue
        bucket = round(round(typical / size) * size, 4)
        vol_by_bucket[bucket] = vol_by_bucket.get(bucket, 0.0) + volume
        vwap_num += typical * volume
        total_vol += volume
    if not vol_by_bucket:
        return {}
    mean_vol = total_vol / len(vol_by_bucket)
    poc = max(vol_by_bucket, key=vol_by_bucket.get)

    # Value area: expand from POC until >= 70% of session volume.
    target = total_vol * 0.70
    above = sorted(b for b in vol_by_bucket if b > poc)
    below = sorted((b for b in vol_by_bucket if b < poc), reverse=True)
    va_vol = vol_by_bucket[poc]
    lo = hi = poc
    ai, bi = 0, 0
    while va_vol < target and (ai < len(above) or bi < len(below)):
        up = above[ai] if ai < len(above) else None
        down = below[bi] if bi < len(below) else None
        if up is not None and (down is None or vol_by_bucket[up] >= vol_by_bucket[down]):
            va_vol += vol_by_bucket[up]
            hi = up
            ai += 1
        else:
            va_vol += vol_by_bucket[down]
            lo = down
            bi += 1

    nodes = {"demand": [], "supply": []}
    for bucket, volume in sorted(vol_by_bucket.items()):
        if volume >= mean_vol * NODE_MULTIPLIER and bucket != poc:
            side = "demand" if bucket < poc else "supply"
            nodes[side].append({"price": bucket, "volume": round(volume)})
    closes = [float(r["Close"]) for _, r in session_bars.iterrows()]
    ema_fast = _ema(closes, EMA_FAST)
    ema_slow = _ema(closes, EMA_SLOW)
    return {
        "poc": poc,
        "vah": hi,
        "val": lo,
        "vwap": round(vwap_num / total_vol, 4) if total_vol else None,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_fast_period": EMA_FAST,
        "ema_slow_period": EMA_SLOW,
        "session_volume": int(total_vol),
        "bars": len(session_bars),
        "developing": len(session_bars) < 12,
        "high": float(session_bars["High"].max()),
        "low": float(session_bars["Low"].min()),
        "close": float(session_bars["Close"].iloc[-1]),
        "demand_nodes": nodes["demand"],
        "supply_nodes": nodes["supply"],
        "bucket_size": size,
    }


def _dist(price: float, level: float) -> float:
    return abs(price - level) / price


def _nearest(nodes: list[dict], price: float, side: str):
    """Nearest node in the direction that traps the price (supply above for
    longs, demand below for shorts)."""
    candidates = [n["price"] for n in nodes if n["price"] > price] if side == "supply" \
        else [n["price"] for n in nodes if n["price"] < price]
    return min(candidates, key=lambda lvl: abs(lvl - price)) if candidates else None


def confluence(price: float, profile: dict, prior: dict | None,
               direction: str = "long") -> dict:
    """Confirm/block verdict from zone structure. Never decides the trade.

    direction: "long" or "short" - only checks blocking in this direction.
    """
    reasons: list[str] = []
    if not profile:
        return {"verdict": "neutral", "reasons": ["no session profile yet"]}
    poc, vah, val = profile["poc"], profile["vah"], profile["val"]
    developing = profile.get("developing")
    if developing:
        reasons.append("session developing - prior profile is the reference")
    ema_fast = profile.get("ema_fast")
    ema_slow = profile.get("ema_slow")
    ema_bullish = (ema_fast is not None and ema_slow is not None
                   and price > ema_fast and price > ema_slow)
    ema_bearish = (ema_fast is not None and ema_slow is not None
                   and price < ema_fast and price < ema_slow)

    if direction == "long":
        blocker = None
        ceiling = _nearest(profile["supply_nodes"], price, "supply")
        if ceiling is not None and _dist(price, ceiling) <= PIN_DISTANCE:
            room = (ceiling - price) / price
            blocker = f"pinned at supply node {ceiling:.2f} (only {room * 100:.2f}% room above)"
        if prior:
            prior_val = prior.get("val")
            prior_vwap = prior.get("vwap")
            floor = min(x for x in (prior_val, prior_vwap, prior.get("low")) if x)
            if price < floor * (1 + BROKEN_DISTANCE) and _dist(price, floor) > BROKEN_DISTANCE:
                blocker = blocker or f"broken below prior structure ({floor:.2f})"
        if ema_bearish:
            blocker = blocker or (f"EMA bearish alignment: price below both "
                                  f"9/21 EMA ({ema_fast:.2f}/{ema_slow:.2f})")
        if blocker:
            reasons.append(blocker)
            return {"verdict": "long_blocked", "reasons": reasons}
    else:
        blocker = None
        floor_node = _nearest(profile["demand_nodes"], price, "demand")
        if floor_node is not None and _dist(price, floor_node) <= PIN_DISTANCE:
            room = (price - floor_node) / price
            blocker = f"pinned at demand node {floor_node:.2f} (only {room * 100:.2f}% room below)"
        if prior:
            prior_vah = prior.get("vah")
            prior_vwap = prior.get("vwap")
            cap = max(x for x in (prior_vah, prior_vwap, prior.get("high")) if x)
            if price > cap * (1 + BROKEN_DISTANCE):
                blocker = blocker or f"extended above prior structure ({cap:.2f})"
        if ema_bullish:
            blocker = blocker or (f"EMA bullish alignment: price above both "
                                  f"9/21 EMA ({ema_fast:.2f}/{ema_slow:.2f})")
        if blocker:
            reasons.append(blocker)
            return {"verdict": "short_blocked", "reasons": reasons}

    where = ("above value area" if price > vah else
             "below value area" if price < val else "inside value area")
    reasons.append(f"POC {poc:.2f} / VA {val:.2f}-{vah:.2f} / price {where}")
    if ema_fast is not None and ema_slow is not None:
        if ema_bullish:
            reasons.append(f"EMA bullish: price above 9/21 EMA ({ema_fast:.2f}/{ema_slow:.2f})")
        elif ema_bearish:
            reasons.append(f"EMA bearish: price below 9/21 EMA ({ema_fast:.2f}/{ema_slow:.2f})")
        else:
            reasons.append(f"EMA mixed: 9/21 EMA {ema_fast:.2f}/{ema_slow:.2f}")
    return {"verdict": "long_ok" if direction == "long" else "short_ok", "reasons": reasons}


def analyze(ticker: str, date: str | None = None, sessions: int = 5,
            direction: str = "long") -> dict:
    import yfinance as yf

    bars = yf.Ticker(ticker).history(period=f"{sessions}d", interval="5m")
    if bars is None or bars.empty:
        return {"ticker": ticker, "error": "no intraday bars"}
    available = sorted({str(i)[:10] for i in bars.index})
    session = date or available[-1]
    if session not in available:
        return {"ticker": ticker, "error": f"no bars for {session}; have {available}"}
    profile = _session_profile(_session_bars(bars, session))
    prior_date = available[available.index(session) - 1] if available.index(session) > 0 else None
    prior = _session_profile(_session_bars(bars, prior_date)) if prior_date else None
    price = profile.get("close") if profile else None
    report = {
        "ticker": ticker, "session": session,
        "prior_session": prior_date,
        "price": price,
        "profile": profile,
        "prior": prior,
        "generated_at": datetime.now(ET).isoformat(),
    }
    if price:
        report["verdict"] = confluence(price, profile, prior, direction)
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="POC + supply/demand zone analysis.")
    p.add_argument("--tickers", required=True, help="comma list, e.g. SPY,NVDA")
    p.add_argument("--date", default=None, help="session date YYYY-MM-DD (default latest)")
    args = p.parse_args(argv)

    out_dir = ZONE_DIR / (args.date or datetime.now(ET).date().isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    for ticker in [t.strip().upper() for t in args.tickers.split(",") if t.strip()]:
        report = analyze(ticker, args.date)
        path = out_dir / f"{ticker}.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        if "error" in report:
            print(f"{ticker}: {report['error']}")
            continue
        v = report.get("verdict", {})
        ema = report.get("profile", {})
        print(f"{ticker} {report['session']}: price {report['price']:.2f} | "
              f"POC {report['profile']['poc']:.2f} | "
              f"VA {report['profile']['val']:.2f}-{report['profile']['vah']:.2f} | "
              f"EMA {ema.get('ema_fast', '?')}/{ema.get('ema_slow', '?')} | "
              f"{v.get('verdict', '?')} ({'; '.join(v.get('reasons', []))[:120]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
