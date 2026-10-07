"""Strategy backtest loop with costs + incubation (walk-forward).

Tests a grid of simple auditable strategy variants over daily bars,
applies commission + slippage on every fill, enforces a minimum-trades
gate, and reports out-of-sample (incubation) results for the in-sample
winners. Stdlib only; price data from Stooq free CSV (no key).

Families (long-only, next-open execution):
  sma_cross(fast, slow)   close>slow after being below -> buy; cross under -> sell
  rsi_meanrev(lb, os, ob) RSI<os -> buy; RSI>ob -> sell
  breakout(window)        close>highest(window) -> buy; close<lowest(window/2) -> sell
  overnight(trend)        buy at close when close>50d SMA -> sell next open (gap capture)

Costs (the video's honest part): --commission $/share/side and
--slippage-bps on fill price, both sides, every trade.

Incubation: first 70% of bars = in-sample (rank), last 30% =
out-of-sample. A variant passes only with >= --min-trades in BOTH
segments. Ranked by in-sample Sharpe, reported with OOS return.

  python3 scripts/strategy_backtest.py --tickers NVDA,SPY --start 2023-01-01
  python3 scripts/strategy_backtest.py --tickers NVDA --wide --min-trades 20
  python3 scripts/strategy_backtest.py --tickers NVDA,AAPL,MSFT --start 2024-01-01 --commission 0.005 --slippage-bps 5

Output: outputs/backtests/<run_id>/{summary.json,leaderboard.csv}; the
paper dashboard shows the latest run when present.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import urllib.request
from datetime import datetime
from pathlib import Path


def fetch_daily(ticker: str, start: str, end: str) -> list[dict]:
    try:
        import yfinance as yf  # noqa: PLC0415
    except ImportError:
        yf = None
    if yf is not None:
        df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=False)
        if df is None or len(df) < 60:
            raise ValueError(f"only {0 if df is None else len(df)} bars for {ticker}; need >=60")
        rows = [{"date": idx.strftime("%Y-%m-%d"), "open": float(r["Open"]), "high": float(r["High"]),
                 "low": float(r["Low"]), "close": float(r["Close"]), "volume": float(r["Volume"])}
                for idx, r in df.iterrows()]
        return rows
    sym = ticker.replace("-", ".").lower()
    url = (f"https://stooq.com/q/d/l/?s={sym}.us&d1={start.replace('-', '')}"
           f"&d2={end.replace('-', '')}&i=d")
    req = urllib.request.Request(url, headers={"User-Agent": "paper-backtest/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", "replace")
    rows = []
    for i, line in enumerate(text.splitlines()):
        if i == 0 or not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 6:
            continue
        rows.append({"date": parts[0], "open": float(parts[1]), "high": float(parts[2]),
                     "low": float(parts[3]), "close": float(parts[4]), "volume": float(parts[5])})
    if len(rows) < 60:
        raise ValueError(f"only {len(rows)} bars for {ticker}; need >=60")
    return rows


def sma(values: list[float], n: int, i: int) -> float | None:
    if i + 1 < n:
        return None
    return sum(values[i - n + 1:i + 1]) / n


def rsi_wilder(closes: list[float], period: int, i: int, _cache: dict) -> float | None:
    key = ("rsi", period)
    arr = _cache.get(key)
    if arr is None:
        gains, losses = [0.0], [0.0]
        for k in range(1, len(closes)):
            ch = closes[k] - closes[k - 1]
            gains.append(max(ch, 0.0))
            losses.append(max(-ch, 0.0))
        avg_g = sum(gains[1:period + 1]) / period
        avg_l = sum(losses[1:period + 1]) / period
        arr = [None] * len(closes)
        for k in range(period, len(closes)):
            if k > period:
                avg_g = (avg_g * (period - 1) + gains[k]) / period
                avg_l = (avg_l * (period - 1) + losses[k]) / period
            arr[k] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
        _cache[key] = arr
    return arr[i]


def signals(variant: dict, bars: list[dict]) -> list[int]:
    """Position target (0/1) per bar using only data available at that close."""
    closes = [b["close"] for b in bars]
    n = len(bars)
    pos = [0] * n
    cache: dict = {}
    kind = variant["family"]
    if kind == "sma_cross":
        f, s = variant["fast"], variant["slow"]
        for i in range(n):
            a, b = sma(closes, f, i), sma(closes, s, i)
            if a is None or b is None:
                continue
            pos[i] = 1 if closes[i] > b and a > b else 0
    elif kind == "rsi_meanrev":
        lb, os_, ob = variant["lb"], variant["os"], variant["ob"]
        holding = 0
        for i in range(n):
            r = rsi_wilder(closes, lb, i, cache)
            if r is None:
                continue
            if r < os_:
                holding = 1
            elif r > ob:
                holding = 0
            pos[i] = holding
    elif kind == "breakout":
        w = variant["window"]
        holding = 0
        for i in range(n):
            if i + 1 < w + 1:
                continue
            hi = max(closes[i - w:i])
            lo = min(closes[max(0, i - w // 2):i])
            if closes[i] > hi:
                holding = 1
            elif closes[i] < lo:
                holding = 0
            pos[i] = holding
    elif kind == "overnight":
        trend = variant["trend"]
        for i in range(n):
            avg = sma(closes, trend, i)
            if avg is None:
                continue
            pos[i] = 1 if closes[i] > avg else 0
    return pos


def _summarize(bars: list[dict], equity: list[float], cash0: float,
               trades: int, wins: int, gross_w: float, gross_l: float,
               holds: list) -> dict:
    total = equity[-1] / cash0 - 1
    bh = bars[-1]["close"] / bars[0]["open"] - 1
    peak, dd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        dd = min(dd, e / peak - 1)
    rets = [(equity[i + 1] / equity[i] - 1) for i in range(len(equity) - 1) if equity[i] > 0]
    vol = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    sharpe = (sum(rets) / len(rets) / (vol or 1e-9) * math.sqrt(252)) if len(rets) > 1 else 0.0
    dates = [b["date"] for b in bars]
    curve = [{"date": d, "equity": round(e / cash0, 4)} for d, e in zip(dates, equity)]
    return {"return": total, "buy_hold": bh, "excess": total - bh, "max_dd": dd,
            "sharpe": sharpe, "trades": trades, "curve": curve,
            "win_rate": (wins / trades) if trades else 0.0,
            "profit_factor": (gross_w / gross_l) if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0),
            "avg_hold_days": (sum(holds) / len(holds)) if holds else 0.0}


def run_overnight(bars: list[dict], pos: list[int], cash0: float,
                  commission: float, slip: float) -> dict:
    """Buy at close, sell next open — captures the overnight gap only."""
    cash, trades, wins, gross_w, gross_l = cash0, 0, 0, 0.0, 0.0
    equity = []
    for i in range(len(bars) - 1):
        if pos[i] == 1 and cash > 0:
            buy_px = bars[i]["close"] * (1 + slip)
            shares = (cash * 0.95) / buy_px
            cost = shares * buy_px + shares * commission
            sell_px = bars[i + 1]["open"] * (1 - slip)
            proceeds = shares * sell_px - shares * commission
            pnl = proceeds - cost
            trades += 1
            if pnl > 0:
                wins += 1
                gross_w += pnl
            else:
                gross_l += -pnl
            cash = cash - cost + proceeds
        equity.append(cash)
    equity.append(cash)
    return _summarize(bars, equity, cash0, trades, wins, gross_w, gross_l, [])


def run_segment(bars: list[dict], pos: list[int], cash0: float,
                commission: float, slip: float) -> dict:
    cash, shares, entry_px = cash0, 0.0, 0.0
    trades, wins, gross_w, gross_l, holds = 0, 0, 0.0, 0.0, []
    equity = []
    holding = 0
    for i in range(len(bars) - 1):
        target = pos[i]  # signal at close i, fill at open i+1
        px = bars[i + 1]["open"]
        if target != holding:
            if target == 1 and cash > 0:
                fill = px * (1 + slip)
                shares = (cash * 0.95) / fill
                cash -= shares * fill + shares * commission
                entry_px = fill
                holding = 1
                entry_i = i
            elif target == 0 and shares > 0:
                fill = px * (1 - slip)
                proceeds = shares * fill - shares * commission
                pnl = proceeds - (cash + shares * entry_px)
                trades += 1
                holds.append(i - entry_i)
                if pnl > 0:
                    wins += 1
                    gross_w += pnl
                else:
                    gross_l += -pnl
                cash = proceeds
                shares = 0.0
                holding = 0
        equity.append(cash + shares * bars[i]["close"])
    equity.append(cash + shares * bars[-1]["close"])
    return _summarize(bars, equity, cash0, trades, wins, gross_w, gross_l, holds)


def grid(wide: bool) -> list[dict]:
    variants = []
    for f, s in ([(10, 20), (10, 50), (20, 50)] + ([(5, 20), (20, 100), (50, 200)] if wide else [])):
        variants.append({"family": "sma_cross", "fast": f, "slow": s})
    for lb, os_, ob in ([(14, 30, 70), (14, 25, 75), (14, 20, 80)] + ([(7, 30, 70), (21, 30, 70)] if wide else [])):
        variants.append({"family": "rsi_meanrev", "lb": lb, "os": os_, "ob": ob})
    for w in ([20, 55] + ([10, 100] if wide else [])):
        variants.append({"family": "breakout", "window": w})
    for t in ([50] + ([20, 200] if wide else [])):
        variants.append({"family": "overnight", "trend": t})
    return variants


def label(v: dict) -> str:
    if v["family"] == "sma_cross":
        return f"sma{v['fast']}/{v['slow']}"
    if v["family"] == "rsi_meanrev":
        return f"rsi{v['lb']}_{v['os']}/{v['ob']}"
    if v["family"] == "overnight":
        return f"overnight{v['trend']}"
    return f"breakout{v['window']}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest grid with costs + incubation.")
    ap.add_argument("--tickers", default="NVDA,SPY")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--cash", type=float, default=10000.0)
    ap.add_argument("--commission", type=float, default=0.005, help="$/share/side")
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--wide", action="store_true", help="expand grid (~6x variants)")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    slip = a.slippage_bps / 10000
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path("outputs/backtests") / run_id
    out.mkdir(parents=True, exist_ok=True)

    all_rows = []
    price_series: dict[str, list] = {}
    for ticker in [t.strip().upper() for t in a.tickers.split(",") if t.strip()]:
        try:
            bars = fetch_daily(ticker, a.start, a.end)
        except Exception as exc:  # noqa: BLE001
            print(f"{ticker}: data failed: {exc}")
            continue
        first = bars[0]["close"]
        price_series[ticker] = [{"date": b["date"],
                                 "close": round(b["close"] / first, 4)} for b in bars]
        cut = int(len(bars) * 0.7)
        for v in grid(a.wide):
            pos = signals(v, bars)
            runner = run_overnight if v["family"] == "overnight" else run_segment
            is_r = runner(bars[:cut], pos[:cut], a.cash, a.commission, slip)
            oos_r = runner(bars[cut:], pos[cut:], a.cash, a.commission, slip)
            passed = is_r["trades"] >= a.min_trades and oos_r["trades"] >= a.min_trades
            all_rows.append({"ticker": ticker, "variant": label(v), **v,
                             "bars": len(bars),
                             "is_return": round(is_r["return"], 4), "is_sharpe": round(is_r["sharpe"], 3),
                             "is_trades": is_r["trades"], "is_curve": is_r["curve"],
                             "oos_return": round(oos_r["return"], 4), "oos_sharpe": round(oos_r["sharpe"], 3),
                             "oos_trades": oos_r["trades"], "oos_max_dd": round(oos_r["max_dd"], 4),
                             "oos_win_rate": round(oos_r["win_rate"], 3),
                             "oos_curve": oos_r["curve"],
                             "incubation_pass": passed and oos_r["return"] > 0})
    eligible = [r for r in all_rows if r["is_trades"] >= a.min_trades]
    eligible.sort(key=lambda r: r["is_sharpe"], reverse=True)
    top = eligible[:a.top]

    (out / "summary.json").write_text(json.dumps({
        "run_id": run_id, "params": vars(a), "variants_tested": len(all_rows),
        "eligible": len(eligible), "top": top, "prices": price_series,
        "incubation_passed": [r for r in top if r["incubation_pass"]]}, indent=2, default=str))
    with open(out / "leaderboard.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "variant", "bars", "is_return", "is_sharpe",
                                           "is_trades", "oos_return", "oos_sharpe", "oos_trades",
                                           "oos_max_dd", "oos_win_rate", "incubation_pass"])
        w.writeheader()
        for r in top:
            w.writerow({k: r[k] for k in w.fieldnames})
    Path("outputs/backtests/latest.json").write_text(json.dumps(
        {"run_id": run_id, "top": top, "prices": price_series}, indent=2, default=str))

    print(f"tested={len(all_rows)} eligible(trades>={a.min_trades})={len(eligible)} -> {out}")
    print(f"{'ticker':6} {'variant':14} {'IS_ret':>7} {'IS_sh':>6} {'IS_tr':>5} | "
          f"{'OOS_ret':>7} {'OOS_sh':>6} {'OOS_tr':>5} {'OOS_dd':>7} pass")
    for r in top:
        flag = "PASS" if r["incubation_pass"] else "----"
        print(f"{r['ticker']:6} {r['variant']:14} {r['is_return']:7.1%} {r['is_sharpe']:6.2f} "
              f"{r['is_trades']:5d} | {r['oos_return']:7.1%} {r['oos_sharpe']:6.2f} "
              f"{r['oos_trades']:5d} {r['oos_max_dd']:7.1%} {flag}")
    if not any(r["incubation_pass"] for r in top):
        print("No variant passed incubation (OOS>0 with min trades). Correct answer: trade nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
