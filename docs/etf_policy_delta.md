# Policy Delta Proposal: index-ETF underlyings (DRAFT — not in effect)

Status: **ENACTED 2026-10-08** on written owner authorization. All five
changes landed together (config, universe, call sites, docs, tests).

## What changes

Purchase universe widens exactly this much:

```text
BEFORE: S&P 500 constituents only (buys)
AFTER:  S&P 500 constituents + allowlisted index ETFs (SPY, QQQ) for buys
```

Everything else is untouched: earnings blackout, caps, session window,
review → ledger → guarded place, long-only options, 0DTE/naked/short
disabled, live off.

## Why these two

- SPY/QQQ options are the most liquid US contracts (penny spreads, deep OI);
  our scout's liquidity filters pass them trivially.
- They are diversified baskets, so single-name blowup risk is lower than any
  constituent — directionally consistent with a conservative universe.
- DIA/IWM explicitly excluded for now (smaller scope, revisit with evidence).

## Code changes required (land together or not at all)

1. `config/approval_routes.json` (+ example): new
   `investment_policy.index_etf_allowlist: ["SPY", "QQQ"]`.
2. `robinhood_tools/universe.py`: `require_member_or_etf(symbol)` honoring
   the allowlist; S&P500 path unchanged.
3. Call sites (`paper_flow.place_signal_order`, `options_flow.place_long_option`,
   scripts): use the ETF-aware check for buys.
4. `docs/system_summary.md` + skill references: universe line updated.
5. Tests: member buy passes, allowlisted ETF buy passes, non-listed ETF
   (e.g. DIA) still blocked, stale allowlist fails closed.

## Risks accepted by approving

- Index options move with the whole market; a regime break hits every
  position at once (correlation = 1 by construction).
- 0DTE-adjacent behavior: weekly SPY/QQQ options decay fast; MIN_DTE=7
  stays mandatory.
- Approval required: reply "authorize ETF underlyings" to enact.
