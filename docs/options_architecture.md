# Options Architecture — requires an explicit owner decision first

> **Policy conflict, read before building.** This repository is equity-only
> by checked-in policy: `AlpacaPaperBackend.review_option_order` and the
> paper simulator raise on any option order, and live buys are restricted
> to S&P500 equity. Options trading therefore requires an explicit,
> human-approved policy change (config + investment-policy docs + risk
> limits), not a quiet code addition. Nothing in this file authorizes
> options trading. Phase 2+ options work starts only after the owner
> records that decision.

## Why options change the risk profile

Long equity can fall ~100%; long options routinely expire at −100% through
theta + IV collapse even when direction is right. That is why the plan
starts with **long calls/puts only** (max loss = premium paid), keeps
0DTE/naked-short/undefined-risk spreads disabled in config, and puts the
deterministic risk engine — not the debate winner — in charge of size.

## Contract selection (Options Scout; proposes, never decides)

Inputs: directional thesis + underlying price + expected move + horizon.
Outputs: ranked contracts with strike, expiration, delta, IV (+ percentile
when available), OI, volume, bid/ask + spread %, break-even, Greeks, and
reasons for rejecting illiquid candidates. Hard filters (config):

```text
MIN_DTE=7, MAX_DTE=60, MAX_SPREAD_PCT, MIN_OPTION_VOLUME,
MIN_OPEN_INTEREST, long-only, no 0DTE, no earnings inside blackout.
```

Anything failing a filter is listed as rejected-with-reason, not silently
dropped — the UI shows what the scout refused and why.

## Pricing and simulation

`SimulationBroker` implements the same `Broker` interface as the real
brokers and models bid/ask, limit fills, partial fills, spread, slippage,
fees, timestamps, Greeks drift, and expiration worthlessness for long
options. Strategies are developed against the simulator; the simulator's
fill logic is itself tested (no fills outside the day's range, no fills
through the spread for free, expiry settles at intrinsic).

## Promotion path (no shortcuts)

```text
EXPERIMENTAL → BACKTESTED → INCUBATING → SHADOW → APPROVED → ACTIVE → RETIRED
```

Backtest (costs + min-trades + OOS pass, as `scripts/strategy_backtest.py`
does today) → simulator season → shadow mode (full pipeline, orders
withheld, outcomes scored) → human-approved activation with position and
daily-loss caps proportional to evidence. A strategy whose shadow/sim
behavior diverges from backtest is quarantined automatically.

## What the LLM may and may not do here

May: read chains/quotes/positions (no credentials), argue direction,
propose contracts, explain decisions, propose strategy changes for the
promotion pipeline. May not: bypass risk, widen limits, enable live mode,
hold positions past `MAX_DTE`/blackout without re-approval, or rewrite
production strategy code (proposals → backtest → incubate → human promote).
