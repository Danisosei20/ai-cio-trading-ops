# Market, Volume, and Execution Signals

Use these signals as confirmation and risk controls, not as standalone reasons to trade.

## Liquidity and Volume

For every candidate and proposed trade, record:

- Current session volume and timestamp
- 20-day and 50-day average daily volume
- Relative volume: current volume divided by time-adjusted typical volume
- Average daily dollar volume
- Bid, ask, absolute spread, and spread percentage
- Proposed order size as a percentage of average daily volume
- Whether volume confirms or contradicts the price move

Fail closed when liquidity data is missing or stale. Avoid a new position when the spread is unusually wide, the order would be material relative to normal volume, or abnormal volume cannot be explained by current news. Prefer limit orders when spread or volatility creates material execution uncertainty.

## Trend, Momentum, and Volatility

Evaluate:

- Price versus 20-day, 50-day, and 200-day moving averages
- 20-day and 60-day returns
- Relative strength versus the S&P 500 and the company’s sector
- 14-day RSI as context for extension, never as a buy/sell trigger by itself
- 20-day realized volatility, ATR, gap risk, and maximum drawdown
- Support/resistance or recent swing levels when they affect entry, stop, or reward/risk

Do not chase a price merely because momentum or volume is strong. Require the fundamental thesis, valuation, portfolio fit, and better-than-2:1 reward/risk hurdle.

## Event and Information Risk

Check the next earnings date, investor day, dividend/ex-date, material SEC filings, guidance changes, analyst revisions, corporate actions, litigation, regulation, and relevant macro releases. Note whether the proposed holding period crosses a binary event and size conservatively or wait when the risk cannot be bounded.

Use a current broker quote, current company/SEC source, current news, and at least one independent reliable source. Record timestamps and links. Treat social sentiment only as a weak secondary indicator and never as primary evidence.

## Execution Plan

State the intended price, order type, time in force, maximum acceptable spread/slippage, invalidation level, initial target or review condition, and estimated portfolio weight. Never convert a technical stop or profit target into an automatic Robinhood order under this skill; broker review and explicit Codex approval remain mandatory.

## Signal Interpretation

Classify each signal as `supportive`, `neutral`, `conflicting`, or `unavailable`. A missing critical signal is not neutral. Surface conflicts explicitly and prefer `No Action Recommended` when the evidence is incomplete or contradictory.
