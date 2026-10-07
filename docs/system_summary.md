# System Summary — AI Paper-Trading Stack

Date: 2026-10-06. Paper-only. Live trading OFF.

## Goal

Autonomous AI paper-trading stack (Alpaca paper money, never live funds):
AI research → backtests → incubation → alert webhooks → auto-execution → one readable UI.

## Components

| Component | File | Job |
|---|---|---|
| Research | `scripts/tradingagents_research.py` | TradingAgents multi-agent LLM debate per ticker (market/news/fundamentals → researchers → trader → risk). Research only, never orders. Working provider: `nvidia` + `moonshotai/kimi-k3`. Last call: NVDA 2026-10-02 → Overweight, entry 227.80, stop 215.86. |
| Auto-trader | `scripts/tradingagents_paper_auto.py` | Research → Buy/Overweight buys (≤$500), Sell/Underweight sells held positions, else holds. Session window 10:15–15:30 ET + market clock, S&P500 check, earnings blackout, review fingerprint, ledger. Refuses to run if `TRADING_ENABLED=true`. Writes `status.json` + `live.log` for the UI. |
| Alert webhook | `scripts/alert_webhook.py` | TradingKit equivalent. `POST /alert` JSON → same gates → paper order. Port 8090, shared-secret auth. Verified: bad secrets and non-S&P500 symbols rejected. |
| Backtest loop | `scripts/strategy_backtest.py` | Strategy grid (SMA cross, RSI mean-reversion, Donchian breakout) with commission + slippage, min-trades gate, 70/30 walk-forward incubation. First run: NVDA `sma20/50` +139% IS → −61% OOS → verdict: trade nothing. |
| UI | `outputs/paper/dashboard.html` (via `scripts/paper_dashboard.py`) | Live stage + run log, account/positions/orders, TradingAgents history, backtest leaderboard, approvals, lifecycles. Refreshes every 15s. No secrets in page. |
| Safety core | `robinhood_tools/` (49 modules) | Paper-only Alpaca transport (live URL rejected), approval ledger (SQLite, atomic reservation), S&P500 gate, risk limits ($500 paper / $25 live caps), kill switches, reconciliation. |

## Safety posture

- `TRADING_MODE=paper_auto`, `TRADING_ENABLED=false`, `PAPER_TRADING_ENABLED=true`.
- S&P500-only buys, DAY limit orders only, no price chasing.
- `.env` git-ignored, chmod 600. Secret scan clean.
- Equity only, except: 2026-10-07 owner authorization permits **long calls/puts
  in paper only** (0DTE, naked, short-premium disabled; read layer live-tested,
  order layer reviewed, first live probe review-only 2026-10-07).

## Known gaps

- No stop-loss/take-profit brackets yet (plain limits only).
- Single-bot (NVDA) only; no multi-strategy roster.
- No FRED key → macro analysis proceeds without macro data.
- NVIDIA NIM models vary in tool/structured-output support; `kimi-k3` works, `llama-3.3-70b` is EOL, `nemotron-70b` fails structured output.
- Stooq CSV now requires JS verification → backtests use yfinance (TradingAgents venv).

## Key commands

```bash
../TradingAgents/.venv/bin/python scripts/tradingagents_paper_auto.py --tickers NVDA --dry-run
../TradingAgents/.venv/bin/python scripts/tradingagents_paper_auto.py --tickers NVDA
ALERT_WEBHOOK_SECRET='...' python3 scripts/alert_webhook.py --port 8090
../TradingAgents/.venv/bin/python scripts/strategy_backtest.py --tickers NVDA,SPY --start 2023-01-01
python3 scripts/paper_dashboard.py --serve --port 8080
```

## Direction under evaluation

Multi-agent options trading desk (Phase 1: audit + architecture docs only).
Policy conflict to resolve with owner: repo is equity-only by design;
options require an explicit, human-approved policy change. No options code
until simulator + deterministic risk engine + audit ledger + tests exist.
