# 2026-11-24 trading session analysis

Prepared **11:42–11:55 ET while the market was open**. End-of-day tasks had not
run; those items are marked **INCOMPLETE** rather than inferred. Paper-only.
Enforcement OFF. ADR 0001/0002 preserved. No trade was authorized or placed.

## A. Executive summary

The desk did not trade because its approved paths had no eligible entry under
the day's actual conditions. This is a **policy/structural outcome, not a data
or broker failure**. Research and scanning worked: 09:45 produced a `proceed`
gate, two intraday cycles ran, nine technical setups were found, and the judge
processed all nine symbols with zero errors. Every resulting candidate was
rejected by an existing control: five by VIX > 25, three by desk-wide
`RISK_OFF`, and eight because bearish equity research is exit-only (there is no
short-entry branch in the live judge path). Alpaca paper shows 0 orders, 0
fills, 0 positions.

Two real deficiencies were found: (1) the committed `market_regime()` signature
does not match its caller, while the live checkout has an uncommitted staged
fix — a clean checkout would silently classify every regime as `UNKNOWN`;
(2) VIX gate values have no retained as-of timestamp and vary 27.3–29.0 within
one session. Neither was changed here: the first is the owner's staged work,
the second needs an approved policy/observability change.

## B. Actual operational state

| Check | Verified value |
|---|---|
| Git baseline | `fc0cf8b` (PR #75 merged), CI green |
| Alpaca paper account | `ACTIVE`, equity `84,247.28`, buying power `336,989.12`, shorting enabled |
| Positions / open orders / 11-24 fills | `0 / 0 / 0` |
| Broker clock (11:46 ET) | open, next close 16:00 ET |
| 09:45 gate | `20261124-094501`, `proceed`, research only |
| 09:35 overnight exit | ran, result 0 |
| 09:50 watchdog | ran, result 0 |
| 10:20 workflow | **no such scheduled task**; no 10:20 decision artifact exists |
| Intraday loop | Running; cycles 10:00 and 10:45; next 12:25 |
| 15:10 options / 15:40 overnight / 15:45 flatten / 16:10 news / 16:30 scoring | **INCOMPLETE — not yet due at report time** |
| Authorization enforcement | OFF; no authorization DB consumed today |
| Overnight entries | dry-run unchanged |

## C. Market conditions

- Regime recorded in the live logs: `RISK_OFF` (SPY 50/200-day trend) at
  `outputs/paper/tradingagents/live.log:806`.
- VIX values used in rejections: **27.4, 28.0, 28.4, 29.0, 27.3** — all above
  the 25 policy ceiling. Source: `current_vix()` returns the latest daily close
  from `^VIX` (`period="2d", interval="1d"`, `option_entry_risk.py:48-57`), with
  **no timestamp or staleness check**. Values differ across calls in the same
  session; no as-of value is retained in any artifact.
- Evidence timestamps: 09:45 research run, 10:45 judge batch, 11:40 judge
  completion. No EOD volatility snapshot exists yet.

## D. Long opportunities

| Symbol | Analyst | Desk | Block | Price/levels generated? |
|---|---|---|---|---|
| ED | Overweight | CALL | `regime RISK_OFF: new risk blocked desk-wide` | No |
| MDT | Overweight | CALL | same | No |
| MSFT | Overweight (judge) | CALL | same | No |

MSFT would also have failed the odds gate (`target_first 0.027` vs 20% minimum),
but the regime gate runs first, so the recorded reason is the regime. No long
entry was eligible; `RISK_OFF` blocks new premium buys desk-wide by design.

## E. Short opportunities

- Intraday scanner found **8 `short_breakdown` setups**: QQQ, SPY, NVDA, AAPL,
  AMD, TSLA, META, AMZN (zone verdict `short_ok` on sampled names). Odds for
  shorts are `available: false — time machine is long-side only`.
- Judge produced bearish decisions for all eight, but the live equity path
  treats bearish signals as **exits only**: `bearish (...) but no position
  held` (`tradingagents_paper_auto.py:861-864`). Zero positions → zero exits.
- `daytrade_engine.py` *does* contain a complete short path (R:R, sizing, short
  book budget, borrow check via `/v2/assets/{sym}`, 15:15 cutoff, risk
  controller), but **it was not running today**; the live loop calls `paper_auto`.
- Broker read-only check: all eight names `shortable=true`,
  `borrow_status=easy_to_borrow`, account `shorting_enabled=true`. Borrow
  availability was **not** the blocker.
- Options-mode bearish names (A, APP, BBY, DLTR, SJM) were new PUT-entry
  attempts blocked by VIX — not exits and not equity shorts.
- Underweight/Sell was never converted into a short by the analyzer
  (`automatic_short: false` for all 16 rows).

## F. Gate analysis

16 candidates were evaluated; all 16 were valid policy/structural rejections,
0 technical failures, 0 approvals.

| Category | Count | Mechanism |
|---|---|---|
| VIX > 25 | 5 | `validate_option_entry()` before quote/contract lookup |
| `RISK_OFF` | 3 | `regime_gate_passes()` before order construction |
| No short entry path | 8 | bearish equity signal is exit-only |
| Technical/environment | 0 | judge 9/9 complete, no scout/auth/broker errors |

The 10:00 cycle produced no setups because only 7 five-minute bars existed —
a session-age rule, not a failure. VIX is confined to new option entries
(calls and puts); equity orders and protective exits never consult it.
`RISK_OFF` blocks new buys but preserves sells/exits and auto-recovers on the
next run because it is recomputed from SPY closes (no latch found).

A separate weakness: the circuit-breaker check runs *before* the sell/exit
carve-out (`tradingagents_paper_auto.py:69-74`), so the documented "exits are
never blocked" guarantee would not hold if the breaker tripped. Not triggered
today; recorded as a residual risk, not fixed without approval.

## G. Missed-opportunity review

New `scripts/missed_opportunity_analysis.py` produced
`outputs/reports/daily/2026-11-24-missed-opportunities.json` (16 rows). Every
row has `entry/stop/target = null` because rejected candidates never generated
them at signal time. Therefore:

- **0 hypothetical outcomes** classified;
- **16 `valid_policy_rejection`** classifications;
- **0 missed winners claimed**.

Optional `--post-bars` accepts timestamped closes for observational movement
only; it can never fill entry/stop/target or use future data. Incomplete paths
and ambiguous intrabar outcomes return `not_determinable`. This directly
satisfies the "do not call a later price move a missed winner" requirement.

## H. Execution reliability

- Research environment: the documented TradingAgents venv
  (`...TradingAgents\.venv\Scripts\python.exe`) imports `tradingagents`; the
  system/desk interpreters do not. PR #75 now verifies before research and
  fails closed; re-verified in this worktree.
- Scanner: 2 cycles, 9 symbols/cycle, 9 setups, 1 judge batch, 55-minute
  research latency for 9 tickers (10:45→11:40) — a real optimization target,
  but no research failure.
- Market data was fresh on read-only recheck (last 5m bar 11:45 ET), though the
  code does not itself record freshness and hardcodes `data_age_sec=0`.
- No broker, authorization, or LLM connection failure in today's artifacts.
- `traded_setups.json` labels 9 rows `entered_at` at judge completion even
  though no order was placed — misleading bookkeeping, flagged for fix.

## I. Strategy research

Evidence-based, **not implemented as production policy**:

1. Add a separately approved RISK_OFF intraday short track (the existing
   `daytrade_engine` short gates and the shadow backtest harness are the base).
   Current 60d real-data replay: 6 shorts, all RISK_ON, net −157.26 after
   costs; **zero RISK_OFF samples** → insufficient evidence.
2. Persist a timestamped VIX as-of and per-symbol quote/contract fields even
   for blocked candidates, so point-in-time replay becomes possible.
3. Reduce research latency with a champion/challenger analyst-round test.
4. Resolve the committed/staged `market_regime()` mismatch before any clean
   checkout is trusted.
5. Fix `entered_at` bookkeeping so judge completion is not recorded as an
   entry.
6. Re-examine circuit-breaker ordering against the exit carve-out.

All changes require documented policy updates, out-of-sample testing and owner
approval. No gate was weakened here.

## J. Testing

New `tests/test_phase8_session_analysis.py`: **22 tests** covering scan counts,
RISK_OFF/VIX classification, long vs bearish-research classification,
Underweight-not-automatic-short, missing quotes, stale bars, valid replay,
look-ahead prevention, incomplete data, shadow/real separation, session cutoff,
idempotent reports, read-only ledger access, dashboard rendering, interpreter
independence, and no-order-submission guarantees.

Full battery: **1,189 tests OK (2 skipped)**; ruff clean; `mypy robinhood_tools`
clean (83 files); secret scan clean; bypass guard PASS; certification harness
**67 PASS / 0 FAIL / 2 SKIP**. One non-reproducing transient test error was
observed on a single full-suite run; two subsequent full runs were green.

New reporting modules: `scripts/missed_opportunity_analysis.py`,
`scripts/daily_trading_review.py`, `scripts/opportunity_analysis.py`; dashboard
endpoint `/api/opportunity-analysis` verified live on a temporary port (status
`ok`, 16 opportunities, 0 orders) with `/trading` rendering the new section.

## K. Git changes

Branch `feat/phase8-session-analysis` from `fc0cf8b`. New files: the four
scripts/tests above, generated daily reports under `outputs/reports/daily/`
(ignored by git, regenerated by the scripts), and this report. PR to be opened
after commit; merge requires CI green. The owner's 10 staged and untracked
files in the main checkout were preserved untouched.

## L. Remaining risks

1. **INCOMPLETE session** — EOD artifacts (15:10/15:40/15:45/16:10/16:30) were
   not due at report time; no end-of-day claim is made.
2. Committed `market_regime()` signature mismatch vs the owner's staged fix;
   not modified here to avoid overwriting their work.
3. VIX has no as-of timestamp; option-gate evidence is weak.
4. No approved short-entry path in the live judge; borrow availability is not
   the blocker.
5. No statistically valid RISK_OFF short backtest sample exists.
6. Windows ACL and real broker rehearsal remain NOT TESTED from Phase 7.4.

## M. Final conclusion

1. **Did the desk behave correctly?** Yes. All 16 candidates were rejected by
   existing, documented controls; no gate was bypassed and no order was placed.
2. **Did it miss independently valid setups?** Eight short setups were real and
   borrowable, but no approved short-entry strategy exists in the live path, so
   they were not independently *executable* opportunities. No missed winner is
   claimed.
3. **Policy, technical error, or strategy limitation?** Policy (VIX, RISK_OFF)
   for five and three candidates; strategy/architecture limitation (no short
   entry branch) for eight. No technical failure today.
4. **Is any approved strategy eligible under RISK_OFF?** No new-risk entry is
   eligible; only protective exits and risk-reducing closes are.
5. **What could improve selection without weakening safety?** Timestamped VIX
   and quote evidence, point-in-time field persistence, latency reduction via
   champion/challenger, and a separately approved shadow short track.
6. **What needs testing/approval?** Any RISK_OFF entry policy change, any VIX
   threshold change, signer-isolation activation, and EOD verification after
   the session completes.

No unattended trading, live trading, or new strategy activation was enabled.
