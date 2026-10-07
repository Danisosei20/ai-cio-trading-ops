# Risk Model — deterministic, non-overridable

The risk engine is code, not an LLM. It returns PASS/FAIL with reasons.
Any FAIL blocks the trade. No agent output can change a FAIL to PASS.

## Layers (all must pass)

1. **Mode/policy layer** — correct environment for the broker
   (`paper_auto`+Alpaca / `live_approval`+Robinhood), kill switches off,
   autonomy enabled for autonomous paths, execution policy permits the action.
2. **Session layer** — weekday, 10:15–15:30 ET (configurable), market clock
   open when `regular_session_only`. Outside window: research allowed,
   placement refused.
3. **Universe layer** — buys only in verified current S&P500 constituents
   (evidence ≤24h, HTTPS source). Existing non-member holdings may be
   trimmed/sold, never added to.
4. **Event layer** — earnings blackout (5 trading days, fail-closed on
   unknown date); economic-event blackout for strategies that declare it;
   material adverse news blocks autonomous entry.
5. **Data-quality layer** — quotes/spreads/volume fresh (≤5min), chain
   complete with bid/ask, timestamps in tolerance, score 100/100 where
   required. Stale or partial data: no trade.
6. **Liquidity layer** — max spread %, max order % of ADV, minimum option
   volume/OI (options phase), fractionable/tradable/active asset checks.
7. **Sizing layer** — per-order cap, per-symbol exposure cap, cash reserve,
   position/sector weights, daily approved-capital cap, max pending
   approvals. Options phase adds: max premium per trade, max account-risk
   fraction (default 0.5%), max contracts, max concurrent positions (3),
   max correlated exposure, max trades/day (3).
8. **Order-shape layer** — limit orders only (autonomous), DAY/`gfd`,
   regular session, positive price, no price chasing (limit == reviewed
   intent). Options phase: long-only to start (max loss = premium);
   0DTE, naked short, and undefined-risk spreads disabled at config.
9. **Confidence layer** — judge confidence ≥ `MIN_CONFIDENCE` (default 75),
   agreement floor, red-team unresolved flags block.
10. **State layer** — no duplicate lifecycle per symbol, cooldowns respected,
    daily loss circuit not tripped, consecutive-loss breaker not tripped,
    no unresolved reconciliation on the symbol.

## Circuit breakers

- **Daily loss:** realized + unrealized beyond limit → `BLOCK_NEW_TRADES`
  for the session. Existing positions keep their stops; no new risk.
- **Consecutive losses (default 3):** suspend → human review required to
  resume. The LLM may not clear this flag.
- **Data/broker/LLM degradation:** stale quotes, chain gaps, broker
  unreachable, model timeout/malformed output → no new trades until healthy.
- **Kill switch:** `emergency-stop` blocks reviews and placements
  immediately; it never liquidates (separate confirmed
  emergency-liquidate action).

## Numbers (conservative defaults, config-owned)

```text
MAX_ACCOUNT_RISK_PER_TRADE = 0.005      (fraction of equity)
MAX_PREMIUM_PER_TRADE      = 500        (paper USD; live lower until proven)
MAX_CONCURRENT_POSITIONS   = 3
MAX_TRADES_PER_DAY         = 3
MAX_CONSECUTIVE_LOSSES     = 3
MIN_CONFIDENCE             = 75
MIN_DTE / MAX_DTE          = 7 / 60     (options; 0DTE disabled)
EARNINGS_BLACKOUT          = 5 trading days
SESSION                    = 10:15–15:30 ET, regular session only
```

## Implementation note

Today these checks live across `risk.py`, `safety.py`, `policy.py`,
`universe.py`, `paper_autonomy.py`, and `runtime.py` guards (see
`docs/dependency_map.md` §4). The options-desk phase consolidates them
behind one `RiskEngine.check(proposal) -> Decision` interface with the same
semantics, so agents and brokers share one source of truth. Consolidation
must not weaken any existing check: each migrated rule keeps its test.
