# Execution-path audit (Phase 7, 2026-11-23)

Scope: every location in `ai-cio-trading-ops` that can create or submit an order.
Read-only audit; line references are from commit `fd6b54b`.

## Choke points

All real paper orders converge on two HTTP calls:

| Broker call | File:line |
|---|---|
| `AlpacaPaperBackend.place_equity_order` → `POST /v2/orders` | `robinhood_tools/alpaca_paper.py:131-140` |
| `AlpacaOptionsBackend.place_option_order` → `POST /v2/orders` (OCC) | `robinhood_tools/alpaca_options.py:247-271` |

They are reached through `RobinhoodTradingService.place_equity_order` (`service.py:66-104`) /
`place_option_order` (`service.py:106-157`) and three shared flows:
`paper_flow.place_signal_order` (`paper_flow.py:49-158`), `options_flow.place_long_option`
(`options_flow.py:61-186`), `options_flow.close_long_option` (`options_flow.py:189-336`).

## Path inventory

| # | Trigger | Class | Strategy / broker / env | Authorization source | Submission | Reads gate verdict? |
|---|---|---|---|---|---|---|
| 1 | `tradingagents_paper_auto.py` equity buy/sell, options via `trade_option_leg` | ENTRY + REDUCE | Research long / options; Alpaca paper; limit | Per-order review ledger created at placement | `place_signal_order`, `place_long_option` | NO when launched directly (10:20 plists, `daytrade_1030.bat`); YES via `intraday_loop`/1020 orchestrator |
| 2 | `alert_webhook.py` `POST /alert` | ENTRY + REDUCE | External strategy; Alpaca paper; limit | HMAC + per-order ledger | `place_signal_order` | NO |
| 3 | `daytrade_engine._place_order` | ENTRY + REDUCE (sell-to-close) | Day-trade long/short; Alpaca paper; bracket limit | Per-order ledger at placement | `place_signal_order` | NO |
| 4 | `daytrade_engine._exit_position` | REDUCE (stop/target, buy-to-cover) | Day-trade exit | Per-order ledger | `place_signal_order` | NO |
| 5 | `flatten_daytrades.py` | REDUCE (EOD flatten) | Day-trade owned qty only | Per-order ledger | `place_signal_order` | NO |
| 6 | `paper_position_guard.py` | REDUCE (stop/target) | Any equity position | Per-order ledger | `place_signal_order` | NO |
| 7 | `overnight_pilot.py --enter` | ENTRY | Overnight gap long; dry-run in Windows `.bat` | Per-order ledger | `daytrade_engine._place_order_with_timeout` | NO |
| 8 | `overnight_pilot.py --exit` | REDUCE | Morning overnight exit | Per-order ledger | `place_signal_order` | NO |
| 9 | `options_pilot.py` | ENTRY | Long call/put; Alpaca paper options | Option approval ledger | `place_long_option` | NO |
| 10 | `options_guard.py` | REDUCE | Sell-to-close stop/target/DTE | Option approval ledger | `close_long_option` | NO (required ExitGuard stage at both slots, verdict-independent) |
| 11 | `options_watch_trade.py` | ENTRY + REDUCE wrapper | 15:10 options watch | Delegated | Delegated | NO |
| 12 | `intraday_loop.py` | GATE + wrapper | Continuous day-trade loop | `desk_verdict()` + per-order ledger | Delegates to #1, #5 | **YES — only path reading the verdict** |
| — | Live Robinhood library (`runtime.py:253`, `mcp_backend.py:34`) | ENTRY | `live_approval`, human confirmation | Review + approval ledger + `confirmed` flag | MCP tool | NO (no production script invokes it) |
| — | Simulators / test brokers | TEST ONLY | SimulationBroker, stubs | N/A | Stub | N/A |

## Paths bypassing deterministic authorization

Entry-increasing paths with no orchestrator authorization: #1 (direct launch), #2, #3, #7, #9, #11.
Exposure-reducing paths also bypass the verdict: #4, #5, #6, #8, #10. This is intentional for
safety-critical exits — they must not be blocked by an expired entry authorization — but they
remain subject to broker review, approval ledger and reconciliation.

`desk_verdict()` (`scripts/intraday_loop.py:94-121`) checks only same-ET-date and
`verdict == "proceed"`; it has no decision id, artifact hashes, validity window, or broker/account
binding. `RunDecision` has none of those fields. Per-order approvals are created at placement
time, not derived from the 09:45 decision.

## Design consequence

A deterministic authorization gateway must sit between signal and `paper_flow`/`options_flow`,
with a fail-closed feature flag. Protective exits keep a separate, explicitly allowlisted route
that requires broker/ledger authentication but not a fresh entry authorization.
