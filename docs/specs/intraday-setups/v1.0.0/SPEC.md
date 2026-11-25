# Canonical Intraday Setup Specification — v1.0.0

Status: **shadow documentation of existing rules** (not a new strategy, not a
promotion, not permission to trade). Effective 2026-11-25. ADR 0001 and ADR
0002 remain unchanged.

This version records the rules already implemented in the desk code so that a
scout/factory comparison can classify disagreements correctly. It does **not**
change live routing, thresholds, risk gates or authorization.

## 1. Vocabulary collision

Two different rules are currently called `long_reclaim`:

| Name used here | Implemented in | Predicate (price vs VWAP and opening range) |
|---|---|---|
| `long_reclaim_scout` (live) | `scripts/daytrade_scout.py:37-39,107-121,160-163` | `price > VWAP and price >= OR_low` |
| `long_reclaim_canonical` (shadow) | `robinhood_tools/bar_validation.py:121-133` | `price > VWAP and price > OR_high and price >= OR_low` |

Likewise for shorts:

| Name used here | Implemented in | Predicate |
|---|---|---|
| `short_breakdown_scout` (live) | `scripts/daytrade_scout.py:10-11,119-120,164-167` | `price < VWAP and price <= OR_high` |
| `short_breakdown_canonical` (shadow) | `robinhood_tools/bar_validation.py:136-148` | `price < VWAP and price < OR_low and price <= OR_high` |

The six November 24 "disagreements" are all the documented stronger-boundary
difference (`OR_high` for longs, `OR_low` for shorts), not implementation bugs.
They were also post-close, duplicated bar sets and therefore not live-eligible
evidence. This classification follows implemented rule definitions only;
independent replay and out-of-sample validation remain required before any
promotion claim.

## 2. Long — `long_reclaim_scout` (live)

- Setup: reclaim of VWAP while holding the opening-range low.
- Opening range: first 6 completed 5-minute bars; requires >= 12 completed
  session bars before the setup is trusted.
- VWAP: volume-weighted typical price of completed session bars; IEX-only
  volume is labeled as such and never presented as consolidated.
- Stop: opening-range low (`scripts/daytrade_engine.py:192,241-244`).
- Target: 2R (`daytrade_engine.py:241-244`).
- Reward-to-risk: >= 1.5 (`daytrade_engine.py:49,302-305`).
- Position risk: 0.25% of equity; max 2 concurrent positions
  (`daytrade_engine.py:48,120-131,641-644`).
- Liquidity: approved watchlist only; missing liquidity score is zero
  (`scripts/daytrade_scout.py:4-5,37`, `opportunity_scoring.py:13-22`).
- Session: 10:30 ET scout after the opening range forms; desk window
  10:15–15:45 ET; flat by 15:45 ET.

## 3. Long — `long_reclaim_canonical` (shadow)

- Same VWAP and opening-range definitions, but price must also break above the
  opening-range **high**.
- Optional relative-strength filter versus SPY
  (`bar_validation.py:132`).
- No live permission; promotion requires replay, out-of-sample validation and
  owner approval.

## 4. Short — `short_breakdown_scout` (live)

- Setup: loss of VWAP while below the opening-range high.
- Stop: opening-range high; target 2R (`daytrade_engine.py:193,245-248`).
- A short is converted to sell-to-close when a long is already held
  (`daytrade_engine.py:210-219`).
- Borrow eligibility is not separately modeled in the repository; unsupported
  evidence fails closed.

## 5. Short — `short_breakdown_canonical` (shadow)

- Price must fall below the opening-range **low** as well as below VWAP and
  OR_high (`bar_validation.py:136-148`).
- Optional relative-weakness filter versus SPY (`bar_validation.py:147`).
- Shadow-only; no live permission.

## 6. Common gates and evidence contract

- Bars: completed, ordered, deduplicated, gap-aware; reject future-dated,
  out-of-session and stale bars (`bar_validation.py:45-118`).
- Freshness: live signal window 300 s by default
  (`robinhood_tools/signal_freshness.py:29-37`); replay-valid does not imply
  live-eligible; post-close analysis is never an entry.
- Regime: SPY 50-day mean classifies RISK_ON/RISK_OFF/CHOP/UNKNOWN with
  fail-closed UNKNOWN on insufficient data (`market_prices.py:32-64`).
  RISK_OFF blocks new risk desk-wide; VIX thresholds are unchanged.
- Zones, odds and risk gates are evaluated downstream
  (`daytrade_engine.py:222-238`); a setup classification alone is never an
  order.
- Evidence snapshots are HMAC-bound (`market_snapshot.py:96-143`); comparisons
  must carry snapshot id, cycle, price, VWAP and freshness so a post-close bar
  cannot be presented as a live decision.

## 7. Change control

Any change to these rules that alters live eligibility requires a new version,
replay and out-of-sample results, independent validation and owner approval.
The comparison tool reports `intentional_strategy_difference` for the boundary
difference above and reserves `implementation_defect` for unexplained
deviations. Agreement alone is not evidence of profitability.
