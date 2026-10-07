# Desk Workflow

Pipeline every signal travels, in order. Any step may end in NO TRADE.

```text
signal (TradingAgents debate / webhook alert / backtest promotion)
  → candidate record (trade_candidates: DISCOVERED)
  → research views (market/news/macro opinions)
  → debate (bull/bear/red opinions; red ≥70 vetoes)
  → judge (STRONG_CALL/CALL/NO_TRADE/PUT/STRONG_PUT)
  → side agreement (buy needs CALL, sell needs PUT)
  → risk gate (RiskEngine or place_signal_order gates)
  → broker review (no order) → ledger create → approve
  → guarded placement (session + clock, atomic reserve, idempotent key)
  → reconcile → position guard (stop/target) → exit → outcome study
```

## Commands

```bash
# research only, no orders
../TradingAgents/.venv/bin/python scripts/tradingagents_research.py \
  --ticker NVDA --date 2026-10-06 --provider nvidia \
  --quick-model moonshotai/kimi-k3 --deep-model moonshotai/kimi-k3

# autonomous paper trader (gated; --dry-run to preview)
../TradingAgents/.venv/bin/python scripts/tradingagents_paper_auto.py --tickers NVDA

# backtest grid with costs + 70/30 incubation
../TradingAgents/.venv/bin/python scripts/strategy_backtest.py \
  --tickers NVDA,SPY --start 2023-01-01 --wide

# exits: report, then enforce
python3 scripts/paper_position_guard.py --check
python3 scripts/paper_position_guard.py --execute

# webhook (needs ALERT_WEBHOOK_SECRET env; buys need earnings_date field)
ALERT_WEBHOOK_SECRET='...' python3 scripts/alert_webhook.py --port 8090

# UI (loopback; port 8100 serves API + app)
python3 scripts/desk_server.py --port 8100
```

## Reading a decision

Use `candidate_timeline(candidate_id)`: candidate row → opinions in order
(market/news/macro, bull/bear/red, judge) → option scans → risk rows with
failed rules. Quote verdicts, confidences, and rule names; never paraphrase
a rejection into an approval.

## Sizing

Paper caps live in `config/approval_routes.json` (`paper_autonomy`:
$500/order and $500/symbol). Buying power is a prescreen, not sizing
authority. Changing any cap is a config change: restate, edit, re-run
`paper-broker-health`, confirm before trading.
