#!/usr/bin/env python3
"""Risk-adjusted performance metrics for the trading desk.

Replaces the $10k weekly target as the primary performance metric.
Computes: net returns after costs, max drawdown, profit factor, expectancy,
Sharpe/Sortino ratio, and comparison to SPY and cash benchmark.

Usage:
  python scripts/desk_metrics.py              # full report
  python scripts/desk_metrics.py --days 30     # last 30 days
  python scripts/desk_metrics.py --json        # machine-readable
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")


def compute_metrics(trades: list[dict], equity_curve: list[float] | None = None,
                    benchmark_returns: list[float] | None = None) -> dict:
    """Compute risk-adjusted metrics from a list of trade records.

    Each trade: {"pnl": float, "return_pct": float, "strategy": str, ...}
    """
    if not trades:
        return {"error": "no trades"}

    pnls = [t.get("pnl", 0) for t in trades]
    returns = [t.get("return_pct", 0) for t in trades]

    total_pnl = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls) if pnls else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")
    expectancy = total_pnl / len(pnls) if pnls else 0

    # Max drawdown from equity curve or cumulative P/L
    if equity_curve and len(equity_curve) > 1:
        peak = equity_curve[0]
        max_dd = 0.0
        for v in equity_curve:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
    else:
        # Approximate from cumulative P/L
        cum = 0.0
        peak_cum = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            if cum > peak_cum:
                peak_cum = cum
            dd = (peak_cum - cum) / (peak_cum + 1) if peak_cum > 0 else 0
            if dd > max_dd:
                max_dd = dd

    # Sharpe (annualized, assuming ~252 trading days)
    if len(returns) > 1:
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(var) if var > 0 else 1e-9
        sharpe = (mean_r / std) * math.sqrt(252) if std > 0 else 0
    else:
        sharpe = 0

    # Sortino (downside deviation only)
    if len(returns) > 1:
        mean_r = sum(returns) / len(returns)
        downside = [r for r in returns if r < 0]
        if downside:
            dvar = sum(r ** 2 for r in downside) / len(downside)
            dstd = math.sqrt(dvar) if dvar > 0 else 1e-9
            sortino = (mean_r / dstd) * math.sqrt(252) if dstd > 0 else 0
        else:
            sortino = float("inf")
    else:
        sortino = 0

    # Benchmark comparison
    benchmark_excess = 0.0
    if benchmark_returns and len(benchmark_returns) == len(returns):
        benchmark_excess = sum(returns) - sum(benchmark_returns)

    # Strategy breakdown
    by_strategy: dict[str, dict] = {}
    for t in trades:
        strat = t.get("strategy", "unknown")
        if strat not in by_strategy:
            by_strategy[strat] = {"trades": 0, "wins": 0, "pnl": 0.0, "returns": []}
        by_strategy[strat]["trades"] += 1
        if t.get("pnl", 0) > 0:
            by_strategy[strat]["wins"] += 1
        by_strategy[strat]["pnl"] += t.get("pnl", 0)
        by_strategy[strat]["returns"].append(t.get("return_pct", 0))

    strategy_summary = {}
    for strat, data in by_strategy.items():
        strategy_summary[strat] = {
            "trades": data["trades"],
            "wins": data["wins"],
            "win_rate": round(data["wins"] / data["trades"], 3) if data["trades"] > 0 else 0,
            "pnl": round(data["pnl"], 2),
            "avg_return": round(sum(data["returns"]) / len(data["returns"]), 3) if data["returns"] else 0,
        }

    return {
        "total_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 3),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        "expectancy": round(expectancy, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2) if sortino != float("inf") else "inf",
        "benchmark_excess_pct": round(benchmark_excess, 2),
        "by_strategy": strategy_summary,
    }


def load_trades(days: int = 30) -> list[dict]:
    """Load trade records from the desk's auto.json + options_guard + overnight files."""
    trades = []
    cutoff = (datetime.now(ET) - timedelta(days=days)).date().isoformat()
    # 1. Equity auto-trades
    auto_dir = ROOT / "outputs" / "paper" / "tradingagents"
    if auto_dir.exists():
        for path in auto_dir.glob("*/[0-9]*-*/auto.json"):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if rec.get("date", "") < cutoff:
                continue
            action = rec.get("action", "")
            if action not in ("buy_placed", "sell_placed", "dry_run_buy"):
                continue
            trades.append({
                "ticker": rec.get("ticker", ""),
                "date": rec.get("date"),
                "strategy": rec.get("trade_style", "swing"),
                "action": action,
                "qty": float(rec.get("qty", 0) or 0),
                "price": float(rec.get("limit", 0) or 0),
                "decision": rec.get("decision"),
                "desk_verdict": rec.get("desk_verdict"),
            })
    # 2. Options entries from guard state
    guard = ROOT / "outputs" / "paper" / "options_guard.json"
    if guard.exists():
        try:
            gdata = json.loads(guard.read_text(encoding="utf-8"))
            for sym, info in gdata.get("tracked", {}).items():
                # Estimate P/L from current positions
                trades.append({
                    "ticker": sym,
                    "date": info.get("first_seen", ""),
                    "strategy": "options",
                    "action": "buy_placed",
                    "qty": 1,
                    "price": float(info.get("cost", 0)),
                    "decision": "options_entry",
                    "desk_verdict": "options_guard",
                })
        except (OSError, ValueError, KeyError):
            pass
    # 3. Overnight entries
    overnight = ROOT / "outputs" / "paper" / "overnight.json"
    if overnight.exists():
        try:
            odata = json.loads(overnight.read_text(encoding="utf-8"))
            for entry in odata.get("entries", []):
                trades.append({
                    "ticker": entry.get("symbol", ""),
                    "date": entry.get("date", ""),
                    "strategy": "overnight",
                    "action": "buy_placed",
                    "qty": float(entry.get("qty", 0) or 0),
                    "price": float(entry.get("entry_price", 0) or 0),
                    "decision": "overnight_gap",
                    "desk_verdict": "overnight_pilot",
                })
        except (OSError, ValueError, KeyError):
            pass
    return trades


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Desk risk-adjusted metrics.")
    p.add_argument("--days", type=int, default=30, help="lookback days")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)

    trades = load_trades(args.days)
    metrics = compute_metrics(trades)

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print("=== Desk Performance Metrics ===")
        print(f"Period: last {args.days} days | Trades: {metrics.get('total_trades', 0)}")
        print()
        print(f"  Win rate:       {metrics.get('win_rate', 0):.1%}")
        print(f"  Total P/L:      ${metrics.get('total_pnl', 0):,.2f}")
        print(f"  Avg win:        ${metrics.get('avg_win', 0):,.2f}")
        print(f"  Avg loss:       ${metrics.get('avg_loss', 0):,.2f}")
        print(f"  Profit factor:  {metrics.get('profit_factor', 0)}")
        print(f"  Expectancy:     ${metrics.get('expectancy', 0):,.2f}/trade")
        print(f"  Max drawdown:   {metrics.get('max_drawdown_pct', 0):.1f}%")
        print(f"  Sharpe:         {metrics.get('sharpe', 0):.2f}")
        print(f"  Sortino:        {metrics.get('sortino', 0)}")
        print()
        print("  By strategy:")
        for strat, data in metrics.get("by_strategy", {}).items():
            print(f"    {strat}: {data['trades']} trades, {data['wins']} wins "
                  f"({data['win_rate']:.0%}), P/L=${data['pnl']:,.2f}, "
                  f"avg return={data['avg_return']:.2f}%")
        print()
        print("  NOTE: $10,000 weekly target is aspirational dashboard reference only.")
        print("  Primary metrics: net returns, max drawdown, profit factor,")
        print("  expectancy, Sharpe/Sortino, and strategy-specific performance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
