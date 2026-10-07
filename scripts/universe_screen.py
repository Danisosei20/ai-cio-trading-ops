#!/usr/bin/env python3
"""Nightly universe screen: rank S&P 500 candidates for the morning run.

Long-only momentum screen with documented scoring (no black box):
  score = 0.5*z(mom20) + 0.3*z(relvol) + 0.2*z(dist52w)
Filters: price>$10, dollar volume>=$10M/day, RSI<80, >=60 bars.
Earnings are NOT screened here (checked per-trade at gate time).

  ../TradingAgents/.venv/bin/python scripts/universe_screen.py \
    --top 20 --out outputs/screener/latest.json
  ../TradingAgents/.venv/bin/python scripts/tradingagents_paper_auto.py \
    --screen --screen-top 2

Output JSON: {"date": ..., "universe": 503, "ranked": [...top...]}.
The auto-trader's --screen flag consumes it (skips held/cooldown symbols
via the ledger at trade time).
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from datetime import date
from pathlib import Path

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_sp500_symbols() -> list[str]:
    req = urllib.request.Request(SP500_URL, headers={"User-Agent": "desk-screener/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        page = resp.read().decode("utf-8", "replace")
    m = re.search(r"List of S&amp;P 500 companies.*?(<table.*?</table>)", page, re.S)
    table = m.group(1) if m else page
    symbols = sorted({s.upper().replace(".", "-")
                      for s in re.findall(r">([A-Z]{1,5}(?:\.[A-Z])?)<", table)
                      if 1 <= len(s) <= 6})
    if len(symbols) < 400:
        raise ValueError(f"S&P500 fetch returned only {len(symbols)} symbols.")
    return symbols


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, loss in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + loss) / period
    return 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)


def features(closes: list[float], volumes: list[float]) -> dict | None:
    """Per-ticker features or None when data is insufficient."""
    n = len(closes)
    if n < 60:
        return None
    avg20 = sum(volumes[-20:]) / 20
    dollar_vol = avg20 * closes[-1]
    if closes[-1] < 10 or dollar_vol < 10_000_000:
        return None
    r = rsi(closes)
    if r is None or r >= 80:
        return None
    mom20 = closes[-1] / closes[-21] - 1
    relvol = volumes[-1] / avg20 if avg20 > 0 else 0
    hi252 = max(closes[-min(n, 252):])
    return {"mom20": mom20, "relvol": relvol,
            "dist52w": closes[-1] / hi252 - 1, "rsi": r,
            "price": closes[-1], "dollar_vol": dollar_vol}


def rank(rows: list[dict], top: int) -> list[dict]:
    """Cross-sectional z-score rank. Pure function, unit-tested."""
    import statistics

    def z(key: str) -> list[float]:
        vals = [r[key] for r in rows]
        mu = statistics.fmean(vals)
        sd = statistics.pstdev(vals) or 1e-9
        return [(v - mu) / sd for v in vals]

    z_mom, z_rv, z_52 = z("mom20"), z("relvol"), z("dist52w")
    scored = [{**r, "score": round(0.5 * a + 0.3 * b + 0.2 * c, 3)}
              for r, a, b, c in zip(rows, z_mom, z_rv, z_52)]
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:top]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rank the S&P 500 for the morning run.")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--out", default="outputs/screener/latest.json")
    a = ap.parse_args(argv)
    import yfinance as yf  # noqa: PLC0415 (TradingAgents venv)

    symbols = fetch_sp500_symbols()
    print(f"universe: {len(symbols)} symbols", flush=True)
    data = yf.download(" ".join(symbols), start=a.start,
                       end=a.end or None, interval="1d", group_by="ticker",
                       auto_adjust=False, threads=True, progress=False)
    rows = []
    for symbol in symbols:
        try:
            frame = data[symbol].dropna()
            closes = [float(v) for v in frame["Close"]]
            volumes = [float(v) for v in frame["Volume"]]
        except Exception:
            continue
        feat = features(closes, volumes)
        if feat:
            rows.append({"symbol": symbol.replace("-", "."), **feat})
    ranked = rank(rows, a.top)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": a.end or date.today().isoformat(),
               "universe": len(symbols), "screened": len(rows), "ranked": ranked}
    out.write_text(json.dumps(payload, indent=2))
    dated = out.parent / f"{payload['date']}.json"
    dated.write_text(json.dumps(payload, indent=2))
    print(f"screened={len(rows)} top={len(ranked)} -> {out}")
    for r in ranked[:10]:
        print(f"{r['symbol']:8} score={r['score']:6.2f} mom20={r['mom20']:7.1%} "
              f"rvol={r['relvol']:5.1f} rsi={r['rsi']:4.1f} ${r['price']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
