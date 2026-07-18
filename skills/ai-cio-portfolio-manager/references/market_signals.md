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

## Public Disclosure Intelligence

Check official House and Senate periodic transaction reports, SEC Form 4 insider transactions, and selected
professional-manager Form 13F filings when available. Compare Berkshire Hathaway, Pershing Square Capital
Management, and Akre Capital Management as distinct long-horizon styles, not as endorsements. Record the filer, symbol, transaction or reporting period,
direction, disclosed value range, filing date, observation time, source URL, and lag. Congressional reports can
arrive up to 45 days after a transaction, and 13F holdings can arrive up to 45 days after quarter end, so neither
reveals a current entry price or proves that a position is still open. Form 4 is more timely, but distinguish
open-market code `P` purchases from awards, gifts, exercises, planned sales, and other non-discretionary activity.

Classify these observations as `supportive`, `neutral`, `conflicting`, or `unavailable`, with zero score
contribution. They may corroborate or challenge a fully independent thesis; they cannot create a candidate,
override news/fundamentals/price/liquidity, weaken any gate, or trigger copy trading. Do not rank politicians by
party or reputation. For professional managers, identify the filer and SEC record rather than relying on a social
media personality or a third-party leaderboard.

Use TradingView as an optional visual cross-check when browser access is available. Record its symbol, exchange,
timeframe, data source, and observation time. Never scrape around authentication, never treat a community idea
or aggregate technical rating as primary evidence, and never let TradingView replace the broker quote, Alpaca
bars, filings, earnings checks, or independent news. A conflicting TradingView chart blocks autonomous paper
entry until reconciled; an unavailable TradingView session is not fabricated. When the browser produces a
structured chart-analysis summary, record it as secondary confirmation only and keep the underlying chart
evidence auditable.

For a panic-seller setup, require elevated relative volume plus a completed VWAP or opening-range reclaim and
multiple higher-low or stabilization bars. Reject the setup when price is still discovering new lows, the spread
is widening, the selloff follows material negative news, or the higher 2.5:1 reward/risk hurdle is not met.

## Execution Plan

State the intended price, order type, time in force, maximum acceptable spread/slippage, invalidation level, initial target or review condition, and estimated portfolio weight. Never convert a technical stop or profit target into an automatic Robinhood order under this skill; broker review and explicit Codex approval remain mandatory.

## Signal Interpretation

Classify each signal as `supportive`, `neutral`, `conflicting`, or `unavailable`. A missing critical signal is not neutral. Surface conflicts explicitly and prefer `No Action Recommended` when the evidence is incomplete or contradictory.
