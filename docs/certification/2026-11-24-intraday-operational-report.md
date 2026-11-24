# 2026-11-24 intraday operational report

Prepared 11:27 ET while the market was open. Paper-only; enforcement OFF.

## 1. Scanner status
`AI-Desk-IntradayLoop` is **Running** (10:00 start, last result 267009 while
still alive). Cycles: 10:00 no new setups; 10:45 produced 9 new setups
`QQQ, SPY, NVDA, AAPL, AMD, MSFT, TSLA, META, AMZN` from `daytrade_scout`.
The judge (`tradingagents_paper_auto.py --day-trade`) is processing them from
10:45 onward, one ticker about every 6 minutes. Scanning continues through
15:30 ET; no new scan was forced and no completed workflow was re-run.

## 2. Trading window
09:45 research gate (completed 09:45→10:24, `verdict: proceed`); intraday
entries 10:00–15:30 ET; entry cutoff 15:15 ET; flatten 15:45 ET
(`AI-Desk-Flatten-1545` next run 15:45). Market close 16:00 ET. There is **no
10:20 scheduled task** in the registered inventory, and no 10:20 orchestrator
decision exists — the 09:45 stage is research-only by design. Options watch
is scheduled for 15:10 ET.

## 3. Long candidates evaluated
`MDT` (Overweight), `ED` (Overweight), `MSFT` (Overweight). All blocked:
`regime RISK_OFF: new risk blocked desk-wide`. No long entries were eligible.

## 4. Short candidates evaluated
Opening-range/VWAP setups (`short_breakdown`): `QQQ, SPY, NVDA, AAPL, AMD,
MSFT, TSLA, META, AMZN`; zone verdict `short_ok` on the sampled names.
Options-mode bearish candidates from the 09:45 research run: `DLTR (VIX 29.0)`,
`BBY (28.4)`, `A (27.4)`, `SJM (27.3)`, `APP (28.0)`. Judge-confirmed bearish
equity names: `QQQ, SPY, NVDA, AAPL, AMD, TSLA` — all skipped as
`bearish (...) but no position held` (bearish equity signals are exits only).

Odds for short setups are `available: false, reason: time machine is long-side
only` — a real gap in the current short path, not a gate bypass.

## 5–6. Approved / blocked and exact reasons
Approved for **entry**: none. Fourteen shadow recommendations were recorded,
all `paper_candidate` under `regime=RISK_OFF`; none became an order.

| Reason | Symbols |
|---|---|
| `option entry blocked: VIX <value> above 25` | A 27.4, SJM 27.3, APP 28.0, BBY 28.4, DLTR 29.0 |
| `regime RISK_OFF: new risk blocked desk-wide` | ED, MDT, MSFT |
| `bearish (...) but no position held` | QQQ, SPY, NVDA, AAPL, AMD, TSLA |

The VIX rule is confined to the options entry path
(`trade_option_leg → validate_option_entry`) and is working as designed.
`RISK_OFF` is intentionally desk-wide for new buys (equity sells remain exits
and are never blocked). Neither policy was changed.

## 7. Actual broker orders / fills
**Zero.** Alpaca paper account `ACTIVE`, no open orders, no positions.
`order_fills` in `cio.db` has no 2026-11-24 rows (latest rows are 2026-11-20).
`risk_decisions` has no rows today. The `trade_candidates` rows marked
`approved` are desk-advisory records, **not** broker submissions.

## 8. Current positions / exposure
Positions `[]`, open orders `[]`, equity `84,247.28`, buying power
`336,989.12`, no trading blocks. Zero gross and net exposure.

## 9. Execution / environment failures
No `ModuleNotFoundError` in the live run: `auto_trade_weekday.bat` sets
`TA_PY` to the TradingAgents venv, which imports `tradingagents` successfully;
the desk venv does not. The pasted error summary came from a checkout launched
with the desk interpreter. PR #75 makes that failure explicit and fail-closed
(both stage resolution and preflight). No broker, authorization or LLM
connection failure was observed today. `AI-Desk-OvernightEnter` last run
11/23 exited 1 (its 15:40 dry-run for today had not run at report time).

## 10. What can safely run under existing approval
- The intraday scanner and judge (research only) — already running.
- Protective exits, 15:10 options guard/exits, 15:45 flatten, reconciliation.
- Shadow research/backtest collection, including the RISK_OFF short track.
- No entries: none are eligible under VIX > 25 and desk-wide RISK_OFF, and
  no approved long/short strategy can open a bearish equity position.

**No trade was authorized or submitted.** Nothing was forced, no gate was
weakened, enforcement remains OFF, and overnight entries remain dry-run.
