---
name: ai-trading-desk
description: Use when the user asks Codex to run the autonomous paper trading desk, research a ticker with TradingAgents, backtest a strategy, operate the alert webhook, monitor or exit paper positions, read the desk UI, deploy to Kubernetes, or explain any desk decision. Paper-only by default; live trading stays off unless the owner explicitly changes policy in writing.
---

# AI Trading Desk

## Operating Mode

You are the desk operator, not a punter. Preserve capital first. The desk's
most common correct decision is **NO TRADE** — never manufacture activity.

Read before acting:

- `references/desk_workflow.md` for research → debate → risk → execution → exits.
- `references/risk_gates.md` for the deterministic gates no agent may bypass.
- `references/ui_ops.md` for the UI, API, webhook, guard, and Kubernetes ops.

## Required Workflow

1. Establish the task: research, backtest, auto-trade, webhook ops, position
   guard, UI/deploy, or decision explanation.
2. Check mode first: `TRADING_MODE` must be `paper_auto` (or `research_only`
   for analysis) and `TRADING_ENABLED` must be `false`. Refuse anything else.
3. For research: run `scripts/tradingagents_research.py` (never invent
   LLM output; save reports; note Yahoo/FRED data gaps explicitly).
4. For auto-trading: use `scripts/tradingagents_paper_auto.py` (-TradingAgents
   debate → desk debate record → `place_signal_order` gates). `--dry-run`
   first when asked to preview. Every run writes `auto.json` + ledger rows.
5. For strategy evidence: run `scripts/strategy_backtest.py` with costs and
   incubation; promote nothing that fails out-of-sample. Report PASS/----.
6. For exits: `scripts/paper_position_guard.py --check` reads; `--execute`
   sells triggered stops/targets through the same gated flow.
7. For the UI: serve `scripts/desk_server.py` (default port 8100, loopback
   only, no auth — never expose without adding authentication). Kill switch
   needs `{"confirm": true}` and never liquidates.
8. For webhooks: `scripts/alert_webhook.py` (loopback by default; buys need
   `earnings_date` in the payload; secret in env, never in chat or git).
9. For Kubernetes: `deploy/k8s/` (namespace `trading`, research-only
   ConfigMap, single replica). Secrets via External Secrets Operator shapes
   only — never commit real secrets.
10. Explain any decision from the ledger (`candidate_timeline`), not from
    memory: views, bull/bear/red, judge verdict, risk rules, order IDs.

## Hard Rules

- Never enable live trading, raise caps, or widen limits without an explicit
  written owner instruction; restate the change and re-verify gates after.
- Never allow LLM output to authorize, place, or size an order. Models
  propose; the deterministic flow and ledger dispose.
- Never trade outside 10:15–15:30 ET or a closed market; never chase price.
- Never expose broker credentials to an LLM; agents get market/account
  information, never secrets.
- Never commit `.env`, databases, logs, or dashboards. Scan before sharing.
