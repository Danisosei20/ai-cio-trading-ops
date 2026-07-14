# Approval Automation

The goal is maximum automation without uncontrolled trading.

Keep one active task per ticker for the entire trade lifecycle. Research, approval routing, placement,
reconciliation, exit monitoring, sale review, and the terminal Slack result belong to that ticker task;
do not create a second task for the same trade. A separate daily scheduler may start or resume a ticker
task, and a monthly reporting automation may aggregate completed lifecycle and learning records.

The daily CIO automation may:

- Read the authorized Alpaca paper account in paper mode or Robinhood Agentic account in live mode.
- Review market and portfolio conditions.
- Produce a portfolio health dashboard.
- Identify `No Action Recommended`, or prepare a trade thesis.
- Send an approval notification through Slack.
- Record one separate paper-only shadow-equity candidate or shadow no-action observation.

The daily CIO automation must not:

- Place a real order without confirmed approval.
- Cancel an order automatically.
- Treat Slack delivery as trade approval by itself.
- Trade a non-agentic account.
- Default an account when multiple accounts exist.
- Create a broker review or approval from shadow-equity activity.
- Continue when required freshness evidence or broker-state reconciliation is incomplete.

## Approval Channel

Slack is the only configured notification route.

Use `config/approval_routes.json`:

```json
{
  "mode": "send",
  "channels": {
    "slack": {
      "enabled": true,
      "channel_id": "${SLACK_CHANNEL_ID}"
    }
  }
}
```

Slack notification is mobile push only. It is not execution approval.

The 120-minute approval window is enforced by the durable approval store. Any change to account, symbol, side, sizing, order type, time in force, price fields, or market-hours setting invalidates the approval. Executed approvals cannot be reused.

## Approval Message Format

Every approval request should include:

- Recommendation: Buy, Sell, Add, Trim, Hold, or No Action
- Approval ID
- Account and masked account number
- Symbol and asset class
- Current broker quote, quote timestamp/source, and intended execution price
- Plain-language reason for buying or selling
- Current S&P 500 membership evidence for purchases
- Current news, company/SEC material, and independent research sources
- Current volume, 20-day and 50-day average volume, relative volume, and average dollar volume
- Bid/ask spread, proposed order size versus average volume, and expected slippage
- Trend/relative-strength context, volatility, invalidation level, and target/review condition
- Next earnings date and other material event risks
- Order type, dollar amount or quantity, and time in force
- Thesis
- Counter-argument
- Probability of success
- Reward/risk
- Catalysts
- Portfolio impact
- Broker review alerts
- Required market data disclosure
- Clear Codex approval instruction

Every daily notice, including no-action notices, starts with `ACTION`, `WHAT YOU SHOULD DO`, `WHY`,
`NEXT REVIEW`, and `BROKER ENVIRONMENT` (`ALPACA PAPER`, `ROBINHOOD LIVE`, or `RESEARCH ONLY`). Follow those
fields with `CHANGED SINCE YESTERDAY` and source-specific
`DATA AS OF` timestamps. Candidates blocked from execution must appear only under
`WATCHLIST ONLY — NOT A BUY RECOMMENDATION`.

Suggested Codex approval phrase:

```text
Approve APPROVAL_ID: place the reviewed [amount/quantity] [symbol] [side] order.
```

## Phone Routing

Phone routing is disabled. Slack mobile push notifications are the phone notification path.

## Daily Automation Prompt

Use this as the Codex automation prompt:

```text
Use $ai-cio-portfolio-manager to run the daily equity CIO portfolio review; options remain prohibited. Read TRADING_MODE before broker access. In paper_auto, run paper-broker-health and use only the authenticated Alpaca paper account at https://paper-api.alpaca.markets with the paper database/dashboard; never call Robinhood or Alpaca live. In live_approval, use only the Robinhood Agentic account with the live database/dashboard. In research_only, create no broker service. Never fall back between brokers. Resume unexpired Slack monitors and the durable recovery plan first. Reconcile the selected broker's positions, open orders, fills, dividends, corporate actions, and uncertain approvals before recommendations. Require a source-specific freshness manifest with timestamps for broker state, quotes, volume/spreads, regime inputs, earnings/events, S&P 500 membership, and research. Read the full selected account, including cost basis, settled and unsettled cash, pending-order commitments, and unrealized gains. Purchase candidates must be verified current S&P 500 constituents using membership evidence no more than 24 hours old. Obtain a current broker quote and research current news, current company or SEC material, and at least one additional reliable source. Evaluate current and average volume, relative volume, dollar liquidity, bid/ask spread, order-size impact, trend, relative strength, volatility, drawdown, upcoming earnings/events, invalidation level, target/review condition, and expected execution quality. Update only the selected mode's dashboard and journal, and recommend No Action unless an idea clears the policy hurdle. Separately record at most one shadow candidate that clears research rules, or shadow no action; shadow activity never creates an approval or broker call. Lead Slack with ACTION, WHAT YOU SHOULD DO, WHY, NEXT REVIEW, BROKER ENVIRONMENT, CHANGED SINCE YESTERDAY, and DATA AS OF. Review profitable positions for thesis, valuation, concentration, taxes, and position-specific targets; profit alone is not an automatic sell instruction. A paper order must match its Alpaca paper review, fingerprint, and durable approval. A live order additionally requires the Robinhood live kill switch and explicit matching Codex approval. Slack notification is never execution approval. At 1, 5, and 20 trading days, update selected-mode and shadow outcomes with S&P 500 benchmark return, excess return, thesis accuracy, and execution slippage; only change durable rules after repeated documented evidence.
```

For Slack mobile push setup, see `docs/slack_required_tools.md`. Slack is a notification route unless a validated Slack reply-reading approval loop is added.

Known operational note: Slack send tools may require a task explicitly opened with `[@slack](plugin://slack@openai-curated-remote)`. The daily CIO automation must include that plugin mention in the same task prompt. Do not create a second Slack notification task; if Slack tools are not exposed in the daily CIO task, fail closed and do not place trades.

## Health Check

In `paper_auto`, run `python3 -m robinhood_tools.cli paper-broker-health`; it performs an account read only. In
`live_approval`, run `python3 scripts/health_check.py --available-tools slack._slack_send_message
--robinhood-read-ok` after the host has independently verified Robinhood read access. These checks validate the
selected route without posting a Slack message. Use an explicitly requested test post only for end-to-end
delivery verification.

Install the independent missed-run watchdog with `python3 scripts/install_watchdog.py`. It stores the Slack bot
token in the login Keychain, runs from Application Support rather than Desktop, and checks the automation memory
at 10:05 ET on weekdays. It sends one deduplicated message to `HEALTH_SLACK_CHANNEL_ID` when the 09:45 review
has not completed after its grace period. A watchdog alert never creates trading authority.

## Production Connector Contract

The connected host must implement `OrderStatusHost` and `SlackReplyHost` from `robinhood_tools.operations`.
The order host returns broker order state plus stable fill IDs; the Slack host reads only the configured approval
thread and acknowledges parsed replies. `NO` may reject. `YES` and sizing never approve or execute.

After sending an approval message, the same ticker task calls `monitor_slack_reply_window` for up to ten
minutes. The user does not need to return to Codex or say “check my reply.” `YES` moves the conversation to
sizing, `NO` rejects, and an exact size ends the monitor with `fresh_review_required` so the same task can run
affordability checks and a new broker review. Live placement remains blocked until explicit matching approval
is given in Codex; the monitor cannot create execution authority.

Run `cio migrate --backup outputs/backups/pre-v5.db` before the first production start. Install the example
launchd service from `deploy/com.openai.ai-cio.plist.example` only after replacing its paths and validating the
connected host. The standalone CLI intentionally has no credentials and therefore returns `No Action` unless
the host injects live screening callbacks.
