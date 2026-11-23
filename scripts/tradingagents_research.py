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
  ../TradingAgents/.venv/bin/python scripts/tradingagents_research.py --ticker NVDA --date 2026-10-02
  ../TradingAgents/.venv/bin/python scripts/tradingagents_research.py --ticker NVDA --date 2026-10-02 --provider nvidia --quick-model nvidia/nemotron-3.5-lightning-30b-a3b --deep-model nvidia/nemotron-3.5-lightning-30b-a3b

Defaults are the desk's NVIDIA NIM judgment tier. They are explicit so this
bridge can never silently fall back to TradingAgents' OpenAI defaults.

Requires LLM API key in environment (OPENAI_API_KEY, NVIDIA_API_KEY, etc).
Does NOT read keys from robinhood-trading-tools .env.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Windows consoles default to cp1252 and crash on non-ASCII LLM output
# (langchain pretty_print): force UTF-8, never fail on an odd glyph.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except (AttributeError, OSError):
    pass


def artifact_dir(ticker: str, date: str, analysts: list[str]) -> Path:
    """Analyst-tagged artifact directory so Research and News cannot collide."""
    tag = "-".join(sorted(str(a) for a in analysts))[:60] or "none"
    return Path("outputs/paper/tradingagents") / ticker / date / tag


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ticker", required=True, help="e.g. NVDA, AAPL, SPY")
    p.add_argument("--date", required=True, help="analysis date YYYY-MM-DD, must be past or today")
    p.add_argument("--provider", default=os.environ.get("TRADINGAGENTS_LLM_PROVIDER", "nvidia"), help="openai|nvidia|anthropic|google|... (see TradingAgents catalog)")
    # Desk model policy: NVIDIA NIM judgment tier. Explicit defaults stop this
    # bridge from silently falling back to TradingAgents' OpenAI defaults
    # (gpt-6-luna / gpt-6-sol), which the desk does not use.
    p.add_argument("--quick-model", default="nvidia/nemotron-3.5-lightning-30b-a3b", help="override quick_think_llm")
    p.add_argument("--deep-model", default="nvidia/nemotron-3.5-lightning-30b-a3b", help="override deep_think_llm")
    p.add_argument("--analysts", default="market,news,fundamentals",
                   help="comma list, default market,news,fundamentals (social omitted for cost/speed)")
    p.add_argument("--llm-timeout", type=float, default=90.0,
                   help="per-request LLM timeout in seconds (default 90; the SDK "
                        "default is 600 and one stalled call can hang a ticker)")
    p.add_argument("--no-save", action="store_true", help="don't write reports to outputs/")
    args = p.parse_args(argv)
    # Desk policy (README, ADR 0001): NVIDIA NIM only. Reject OpenAI from any
    # source — flag, .env or default — so a stray key can never switch models.
    if str(args.provider).strip().lower() == "openai":
        p.error("OpenAI is not an approved desk provider (NVIDIA NIM only)")
    return args


def _load_llm_env() -> None:
    """Export LLM keys from ../TradingAgents/.env (if present) into os.environ.

    The research bridge never hardcodes keys; it only lifts ones the user
    stored in the TradingAgents checkout. Existing environment values win.
    """
    import os
    from pathlib import Path as _Path

    dotenv = _Path(__file__).resolve().parents[1].parent / "TradingAgents" / ".env"
    if not dotenv.exists():
        return
    try:
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value:
                os.environ.setdefault(key, value)
    except OSError:
        pass


def main(argv=None) -> int:
    # Load LLM env vars from ../TradingAgents/.env before parsing args so that
    # TRADINGAGENTS_LLM_PROVIDER (and any API key) are visible to the default.
    _load_llm_env()
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
    # Self-learning: pin the memory log inside our outputs so decisions,
    # settlements, and reflections live with the desk (and settle_memory.py).
    memory_path = Path(__file__).resolve().parents[1] / "outputs" / "learning" / "trading_memory.md"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    config["memory_log_path"] = str(memory_path)
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
    # Bound each provider call. Without this the OpenAI SDK's 600s read default
    # lets one stalled NIM request consume the whole ticker budget.
    if config.get("timeout") is None:
        config["timeout"] = args.llm_timeout
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
        out_dir = artifact_dir(ticker, args.date, selected)
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
