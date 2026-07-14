# Robinhood Workflow

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

Then ask for explicit approval before placement.

## Equity Placement

Call `_place_equity_order` only after explicit approval in the current conversation.

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

Before placement, atomically reserve the approved order in a transactional store. After any uncertain transport failure, do not retry automatically: mark it for reconciliation and read the broker’s order state first. Record queued, confirmed, partially filled, filled, rejected, and cancelled outcomes plus average fill price and execution slippage.

Keep live trading behind a separate default-off kill switch and exercise the complete workflow in paper mode first. Enabling the switch is necessary operational state, never user approval and never a substitute for broker review or the matching Codex approval ID. Make daily scheduled runs idempotent per account and date so duplicate workers cannot send duplicate approvals.

## Cancellation

Before cancellation, resolve the order with `_get_equity_orders` or `_get_option_orders` if needed. Present the order and ask for explicit cancellation approval. Call cancellation only after approval.

## Options

Default policy prohibits options. If the user asks for options anyway, explain that the standing CIO policy blocks options. Only review options if the user explicitly overrides the policy in writing and the account/tool rules allow it. Do not place options under this skill’s default policy.
