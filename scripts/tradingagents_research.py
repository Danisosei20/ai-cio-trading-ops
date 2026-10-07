"""TradingAgents research bridge (paper-aligned, no live orders).

Uses tauricresearch/TradingAgents multi-agent graph for research only:
analysts -> researchers debate -> trader -> risk -> portfolio manager.

Safety:
- Never places Robinhood live orders.
- Output is a research report + JSON decision under outputs/paper/tradingagents/.
- Any paper execution must still go through robinhood_tools:
  S&P500 check, risk limits ($500 paper cap), Alpaca broker review,
  approval ledger, reconciliation. See README + config/approval_routes.json.

Usage (after `pip install -e ../TradingAgents` in its own venv):
  ../TradingAgents/.venv/bin/python scripts/tradingagents_research.py --ticker NVDA --date 2026-10-02 --provider openai
  ../TradingAgents/.venv/bin/python scripts/tradingagents_research.py --ticker NVDA --date 2026-10-02 --provider nvidia --quick-model meta/llama-3.3-70b-instruct --deep-model meta/llama-3.3-70b-instruct

Requires LLM API key in environment (OPENAI_API_KEY, NVIDIA_API_KEY, etc).
Does NOT read keys from robinhood-trading-tools .env.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ticker", required=True, help="e.g. NVDA, AAPL, SPY")
    p.add_argument("--date", required=True, help="analysis date YYYY-MM-DD, must be past or today")
    p.add_argument("--provider", default="openai", help="openai|nvidia|anthropic|google|... (see TradingAgents catalog)")
    p.add_argument("--quick-model", default=None, help="override quick_think_llm")
    p.add_argument("--deep-model", default=None, help="override deep_think_llm")
    p.add_argument("--analysts", default="market,news,fundamentals",
                   help="comma list, default market,news,fundamentals (social omitted for cost/speed)")
    p.add_argument("--no-save", action="store_true", help="don't write reports to outputs/")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    ticker = args.ticker.strip().upper()
    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.default_config import DEFAULT_CONFIG
    except ImportError:
        print("TradingAgents not installed. Run: ../TradingAgents/.venv/bin/pip install -e ../TradingAgents", file=sys.stderr)
        return 2

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = args.provider
    if args.quick_model:
        config["quick_think_llm"] = args.quick_model
    if args.deep_model:
        config["deep_think_llm"] = args.deep_model
    # Keep debates short/cheap for first paper-aligned run
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    # Ride out bursty 504/429 throttling on hosted NIM endpoints
    if config.get("llm_max_retries") is None:
        config["llm_max_retries"] = 6
    # Shorter tool loops reduce gateway timeouts on large analyst prompts
    if config.get("max_tool_rounds", 20) > 10:
        config["max_tool_rounds"] = 10

    selected = [a.strip() for a in args.analysts.split(",") if a.strip()]
    print(f"Running TradingAgents research: {ticker} {args.date} provider={config['llm_provider']} "
          f"quick={config['quick_think_llm']} deep={config['deep_think_llm']} analysts={selected}")

    ta = TradingAgentsGraph(selected_analysts=selected, debug=True, config=config)
    state, decision = ta.propagate(ticker, args.date)
    print(f"Decision: {decision}")

    if not args.no_save:
        out_dir = Path("outputs/paper/tradingagents") / ticker / args.date
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "decision.json").write_text(json.dumps({
            "ticker": ticker,
            "date": args.date,
            "provider": config["llm_provider"],
            "quick_model": config["quick_think_llm"],
            "deep_model": config["deep_think_llm"],
            "analysts": selected,
            "decision": decision,
        }, indent=2, default=str))
        try:
            ta.save_reports(state, ticker)
            print(f"Full TradingAgents reports saved via ta.save_reports (see ~/.tradingagents/logs + {out_dir})")
        except Exception as e:
            print(f"save_reports failed (non-fatal): {e}", file=sys.stderr)
        print(f"Research JSON: {out_dir / 'decision.json'}")
        print("NEXT: review report manually. To paper-trade, run robinhood_tools daily-review/paper flow separately; "
              "this script never creates broker reviews or approvals.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
