# Investment Policy

## Objective

Maximize long-term, after-tax, risk-adjusted returns while preserving capital through disciplined, evidence-based investing.

Do not maximize activity. If no opportunity clearly qualifies, the answer is:

**No Action Recommended.**

## Review Inputs

Evaluate the full available portfolio and market context:

- Revenue, EPS, and free cash flow growth
- ROIC, ROE, margin level and margin trend
- Balance sheet quality, debt, cash, liquidity
- Capital allocation, dilution, buybacks, dividends
- Competitive advantage and industry structure
- Insider and institutional activity when available
- Analyst revisions and estimate direction
- Relative strength versus S&P 500 and sector
- Valuation versus peers and own history
- Macro regime, rates, inflation, labor, credit, liquidity
- Relevant news, earnings, regulatory, and geopolitical risks
- Current volume versus 20-day and 50-day averages, relative volume, and average dollar volume
- Bid/ask spread, proposed order size versus normal volume, and expected slippage
- Price trend, relative strength, realized volatility, ATR, drawdown, and gap risk
- Upcoming earnings, filings, dividends, and other binary events

Use current data for time-sensitive facts.

Persist a source-specific freshness manifest with observation timestamps for broker account state,
positions/orders/fills, quotes/spreads/volume, regime inputs, earnings and corporate events, index membership,
and research/news/filings. A missing critical source is not neutral; name it and fail closed.

## Eligible Purchase Universe

Only recommend or review purchases of companies verified as current S&P 500 constituents using membership evidence refreshed within the prior 24 hours. Because index membership changes, do not rely on a hard-coded historical list. Record the membership source and observation time. Existing holdings outside the index may be held, trimmed, or sold to reduce legacy exposure, but never added to under this policy. Broad-market ETFs, options, crypto, and non-S&P 500 stocks are not eligible purchases.

Before judging an eligible candidate, obtain a current broker quote and review current company news, the latest company filing or investor material, and at least one additional independent reliable source. Distinguish facts from inference and record source URLs and timestamps.

## Investment Hurdle

Only propose new capital when all are true:

- Clear thesis
- Improving fundamentals
- Sufficient liquidity
- Durable advantage or structural edge
- Reward-to-risk better than 2:1
- Expected risk-adjusted return better than an S&P 500 ETF
- Fits risk limits and tax awareness

## Scores

Use 0-100 scoring:

- 90-100: exceptional; eligible for buy/add if risk fits
- 75-89: strong; hold or monitor
- 60-74: acceptable; hold, no new capital
- 40-59: weak; consider trim if thesis worsens
- Below 40: broken or high risk; consider sell

Suggested weights:

- Business quality: 20%
- Growth and revisions: 15%
- Financial strength: 15%
- Valuation/risk-reward: 20%
- Market confirmation (volume, trend, relative strength): 10%
- Liquidity, volatility, event, and execution risk: 10%
- Portfolio fit, tax, and concentration risk: 10%

## Required Recommendation Content

For each holding or candidate include:

- Recommendation: Hold, Add, Trim, Sell, or No Action
- Thesis status: strengthening, unchanged, weakening, or broken
- Score and reason for score
- Bull case and bear case
- Counter-argument
- Probability of success
- Catalysts
- Portfolio impact
- Risk/reward
- Tax notes when relevant
- Bias check

## Risk Limits

- No margin
- No options
- No leveraged ETFs
- No inverse ETFs
- No short selling
- Avoid concentrated speculative positions
- Watch sector concentration and correlation
- Prefer cash over weak opportunities

Protect cash limits using settled cash after subtracting unsettled funds and capital already committed to pending
orders. Reconcile broker positions, open orders, fills, dividends, and corporate actions against durable ticker
lifecycles before any new recommendation. Unexplained drift blocks new recommendations until reconciled.

## Shadow Equity Learning

When live portfolio limits block execution, the daily process may record at most one separate paper-only equity
candidate that otherwise clears the research, universe, earnings, freshness, regime, and score rules. If none
qualifies, record shadow no action. Shadow activity never creates a broker review, approval, Slack execution
request, or live order. Measure 1-, 5-, and 20-trading-day outcomes against the underlying decision and S&P 500.

## Profitable Position Reviews

Review the full portfolio, cost basis, unrealized gain, holding period, taxes, thesis status, valuation, catalysts, and portfolio weight. A positive gain alone is not a sell signal. Recommend `Trim` or `Sell` when a position-specific target is reached, valuation becomes excessive, portfolio risk is too concentrated, or the thesis weakens. Never sell automatically: run a broker review, send the Slack notification, and require explicit Codex approval with the matching approval ID.

## Dashboard

Every review includes:

- Portfolio health score
- Market regime
- Cash allocation
- Sector allocation
- Largest positions
- Diversification score
- Risk score
- Quality score
- Growth score
- Valuation score
- Momentum score
- Liquidity and execution score
- Volume confirmation and relative-volume status
- Volatility, drawdown, and event-risk status
- Major risks
- Recommended actions
