# Paper and Live Broker Workflow

## Mode Routing

Use no broker in `research_only`. Use Alpaca's authenticated paper endpoint only in `paper_auto`; reject the
Alpaca live endpoint and never fall back to Robinhood or a local simulated fill. Use Robinhood only in
`live_approval`. Keep Alpaca paper and Robinhood live credentials, databases, dashboards, account state,
orders, fills, and reconciliation records separate.

Before an Alpaca paper session, run the repository's read-only `paper-broker-health` command and show only the
masked account identifier. Missing/rejected credentials, an inactive/blocked account, or a non-paper endpoint
blocks paper activity. Autonomous paper orders require an exact review fingerprint and durable internal policy
authorization so the workflow exercises the same tamper, deduplication, and reconciliation controls without a
human approval pause. Keep the separate paper execution switch default-off in new installations.
Record successful connectivity as environment-specific read-only evidence using only the observation time and
masked account identifier. When the official market clock is closed, limit the smoke test to connectivity,
reconciliation, and safety guards; wait for complete fresh market-session inputs before creating an order
review, approval, or Slack execution notice.

## Account Rules

Use Robinhood `_get_accounts` before trading if the account number is not already explicitly and safely known from the current conversation.

When presenting accounts, mask all but the last four digits. When calling tools, pass the full account number returned by the tool.

Only trade or cancel in accounts where `agentic_allowed=true`. If multiple accounts exist, ask the user to choose unless the user clearly named the account, such as the account nickname.

## Equity Order Review

For equity order review, call `_review_equity_order` by default before placement.

Surface:

- Account label and masked account number
- Symbol
- Side
- Type
- Dollar amount or quantity
- Time in force
- Estimated execution details
- Broker alerts
- Required market data disclosure verbatim

In live mode, ask for explicit approval before placement. In autonomous paper mode, continue only through the
paper executor after every deterministic gate passes.

## Equity Placement

Call a live `_place_equity_order` only after explicit approval in the current conversation. In autonomous paper
mode, the internal policy authorization may execute without user approval only when the regular session is
open, the reviewed limit price is unchanged, and the separate $500 order and symbol-exposure caps pass.

Generate a fresh UUID `ref_id` for the first placement attempt. Reuse the same `ref_id` only for retrying the same logical order after transient transport failure.

After placement, report:

- Order ID
- State
- Symbol
- Side
- Type
- Dollar amount or quantity
- Average price if filled
- Reminder that queued/confirmed orders may not be filled

Update the journal.

For autonomous paper actions, send Slack after submission. State paper environment, exact limit price and size,
broker status, rationale, counterargument, liquidity, chart/news checks, exit plan, order fingerprint, and broker
order ID. Never say filled or profitable until reconciliation proves it.

Before placement, atomically reserve the approved order in a transactional store. After any uncertain transport failure, do not retry automatically: mark it for reconciliation and read the broker’s order state first. Record queued, confirmed, partially filled, filled, rejected, and cancelled outcomes plus average fill price and execution slippage.

Keep live Robinhood trading behind a separate default-off kill switch and exercise the complete workflow with
Alpaca paper first. Enabling the switch is necessary operational state, never user approval and never a
substitute for broker review or the matching Codex approval ID. Make daily scheduled runs idempotent per
account and date so duplicate workers cannot send duplicate approvals.

## Cancellation

Before cancellation, resolve the order with the broker. Live cancellation requires explicit approval. An
autonomous paper cancellation may proceed under the paper policy and must send an accurate post-action Slack
summary.

## Options

Default policy prohibits options. If the user asks for options anyway, explain that the standing CIO policy blocks options. Only review options if the user explicitly overrides the policy in writing and the account/tool rules allow it. Do not place options under this skill’s default policy.
