# Approval Routing

Use approval routing when the user wants daily CIO reviews or trade approval requests sent to Slack.

## Rule

Slack notifications are not trade approval by themselves. Do not place or cancel a real Robinhood order unless the user explicitly approves in Codex after seeing the broker review.

Autonomous Alpaca paper execution is the only exception to the human approval pause. It uses an internal policy
authorization and sends Slack after the broker action; Slack never triggers or changes the paper action.

## Supported Route

- Slack: create or send a message when a channel/user ID is known and Slack tools are exposed. Treat Slack as mobile-push notification unless a validated Slack reply-reading approval loop exists.
- Phone: use Slack mobile push notifications.

Do not guess Slack channel IDs.

## Message Content

Every daily notice starts with `ACTION`, `WHAT YOU SHOULD DO`, `WHY`, `NEXT REVIEW`, and `LIVE TRADING`.
Then show `CHANGED SINCE YESTERDAY`, source-specific `DATA AS OF` timestamps, and any blocked candidates under
`WATCHLIST ONLY — NOT A BUY RECOMMENDATION`. When no order was reviewed, say
`None — no order exists` for both approval ID and order fingerprint.

Approval requests must include:

- Recommendation and score
- Approval ID
- Account label and masked account number
- Symbol, side, order type, dollar amount or quantity
- Current broker quote with timestamp and source
- Intended purchase or sale price
- Plain-language reason for the transaction
- Current S&P 500 membership source for purchases
- Current news and research source links
- Current volume, 20-day/50-day average volume, relative volume, and dollar liquidity
- Bid/ask spread, proposed order size versus average volume, and expected slippage
- Trend, relative strength, volatility, drawdown, and event-risk summary
- Invalidation level and target/review condition
- Thesis and counter-argument
- Probability and reward/risk
- Catalysts and portfolio impact
- Broker review alerts
- Required market data disclosure verbatim
- Exact Codex approval phrase with approval ID
- Current settled buying power, proposed cost, and estimated buying power remaining after the order
- Exact reviewed sizing in dollars or shares, plus instructions to request a fresh Codex review for any different size

If buying power is insufficient, send a clearly labeled Slack notice with available buying power, requested cost, shortfall, and a non-binding affordable-size estimate when possible. State `No Approval Created`, do not call broker review or placement, and instruct the user to specify a smaller exact dollar amount or share quantity in Codex. Never accept sizing changes or execution approval from Slack.

## Automation Behavior

Daily automation should:

1. Resume unexpired Slack monitors and durable recovery work.
2. Reconcile uncertain broker state and require complete freshness evidence.
3. Run the CIO review.
4. Update dashboard, journal, daily changes, and the isolated shadow-equity record.
5. Say `No Action Recommended` if nothing clears the hurdle.
6. If a trade clears the hurdle, run review-only first and persist the exact fingerprint.
7. In autonomous paper mode, create an internal policy authorization, atomically reserve and place the unchanged limit order, reconcile, then send a post-trade summary to `SLACK_CHANNEL_ID`.
8. In live mode, send an approval notification and stop before placement.
9. Before live placement, verify the user approved in Codex with the matching approval ID and unchanged parameters.
10. Never open a Slack reply monitor for an already executed autonomous paper action.

Slack mobile push can notify the user, but do not accept Slack as execution approval unless a specific Slack reply-reading workflow has been implemented and tested.

An event-scoped Slack reply monitor may run in the same active Codex task for up to 10 minutes after an approval message. Do not create separate recurring monitoring tasks. It may parse and acknowledge non-executable sizing commands, but it must deduplicate messages and explicitly block approval, buy, sell, placement, and cancellation commands. If no response arrives within 10 minutes, reject the linked pending approval. Delete temporary monitor state after rejection, cancellation, or execution. Do not convert a Slack reply into an approval record or broker call. Direct the user back to Codex for any real sizing or approval workflow.

Keep the complete lifecycle for one ticker in one task, named for that ticker when task naming is available. Research, preview, Slack routing, approval, placement, reconciliation, exit review, sale, and terminal profit/loss notification must resume the same task. Daily scheduling and monthly aggregate reporting may be separate, but must not create competing per-trade tasks.

The reply monitor may treat `NO` as authorization to reject the specifically linked pending, unexecuted approval, because rejection cannot execute a trade. `YES` means only “continue to sizing”: ask for an exact dollar amount or share quantity. A sizing reply must be checked against buying power and then routed to Codex for a fresh broker review. Never interpret `YES` or sizing as placement approval. Send `Trade successful` only after Codex-authorized placement and a successful broker response; distinguish queued, partially filled, filled, rejected, and cancelled states accurately.

## Durable Approval Gate

Persist every broker-reviewed trade under a unique approval ID with its review ID, exact order fingerprint, creation time, expiration time, and state. Placement must fail closed unless the record is approved in Codex, unexpired, unused, and matches every reviewed order parameter. Any change to account, symbol, side, sizing, order type, time in force, price fields, or market-hours setting requires a fresh broker review and approval. Never expose a production review-bypass flag.

Record Slack delivery status, channel, message timestamp/link, retry count, and errors. Delivery failure must not weaken the placement gate. For a fixed validated channel ID, require only the send tool; require user-search or conversation-creation tools only when the workflow actually resolves a new destination.

Prefer a non-posting health check for routine monitoring: validate route configuration, Slack send-tool exposure, and Robinhood read access. Send test messages only when explicitly requested for end-to-end delivery verification.

The CIO task must expose Robinhood and Slack in the same task. Include `[@slack](plugin://slack@openai-curated-remote)` in the task prompt when Slack notifications are required. Do not create a second Slack notification task. If Slack tools are not exposed in the current CIO task, fail closed and report that Slack approval notification is unavailable in this task.

Treat successful test delivery as environment-specific operational evidence, not durable skill configuration. Store the destination only in local environment configuration.
