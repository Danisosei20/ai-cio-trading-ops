# Robinhood Trading Tool Layer

This project contains a safety-gated tool layer for the missing Robinhood trading capabilities:

- `review_equity_order`
- `place_equity_order` / `place_stock_order`
- `cancel_equity_order` / `cancel_stock_order`
- `review_option_order`

The code deliberately separates local tool policy from the real Robinhood connector. The `RobinhoodBackend` protocol in `robinhood_tools/client.py` is the adapter boundary a live connector must implement.

## MCP Endpoint

The project is configured for the Robinhood trading MCP endpoint:

```json
{
  "mcpServers": {
    "robinhood-trading": {
      "url": "https://agent.robinhood.com/mcp/trading"
    }
  }
}
```

The configuration lives in `mcp.json`. The same endpoint and tool-name mapping are exported from `robinhood_tools/mcp_config.py`.

Current mapped MCP tools:

- `review_equity_order` -> `_review_equity_order`
- `place_equity_order` / `place_stock_order` -> `_place_equity_order`
- `cancel_equity_order` / `cancel_stock_order` -> `_cancel_equity_order`
- `review_option_order` -> `_review_option_order`

The adapter in `robinhood_tools/mcp_backend.py` translates local safe request models into the Robinhood MCP parameter names, such as `account_id` -> `account_number`, `notional` -> `dollar_amount`, and `stop` -> `stop_market`.

## Safety Requirements

- Only accounts with `agentic_allowed=true` may review, place, or cancel orders.
- If multiple Robinhood accounts are available, `account_id` is required. The service will not silently default to an account.
- Equity placement requires a durable approval record, a matching broker `review_id`, an unexpired Codex `approval_id`, and unchanged order parameters. There is no production review bypass.
- Approval records are fingerprinted, atomically persisted, and single-use; duplicate execution attempts are blocked.
- Real placement and cancellation always require `confirmed=true`, representing explicit user approval after the final review.
- Cancellation requires the order to belong to the requested account.
- Filled, cancelled, and rejected orders cannot be cancelled.
- Option order support is review-only in this layer. Option placement is intentionally excluded.
- Purchase review requires host-supplied S&P 500 membership evidence observed within the prior 24 hours. A hard-coded constituent list is not accepted as durable policy.
- Existing non-index holdings may still be sold to exit legacy exposure; they cannot be purchased or added to.

## Approval Automation

For scheduled CIO reviews and Slack approval routing, see `docs/approval_automation.md`.

Automation may draft or send approval requests, but it must not place or cancel real orders automatically. Approval routing is notification only; the final Robinhood placement/cancellation still requires explicit user approval after the broker review.

## Why Tools May Still Be Unavailable

These definitions do not grant backend access by themselves. In a live ChatGPT/Codex connector environment, a tool can remain unavailable if any of the following are missing:

- The Robinhood connector is not installed or mounted in the current project.
- The user has not authorized the connector.
- The connector exposes read-only scopes but not trade-write scopes.
- Backend entitlements have not enabled agentic trading for the user or account.
- The account is not marked `agentic_allowed=true`.
- The connector vendor has not exposed the specific operation, such as equity placement or cancellation, to this session.
- A compliance, risk, or rollout gate blocks live trading despite portfolio read access working.

In those cases the service should surface `ConnectorUnavailable`, `AuthorizationRequired`, or `EntitlementMissing` rather than pretending a trade was submitted.

## Integration Notes

Connect a generic live backend by implementing `RobinhoodBackend`:

```python
class LiveRobinhoodBackend:
    def list_accounts(self): ...
    def review_equity_order(self, request): ...
    def place_equity_order(self, request, review_id): ...
    def get_equity_order(self, order_id): ...
    def cancel_equity_order(self, order_id): ...
    def review_option_order(self, request): ...
```

Then wrap it:

```python
service = RobinhoodTradingService(LiveRobinhoodBackend(...))
```

The tool host should pass `confirmed=true` only after the user explicitly approves the exact order or cancellation. Construct `RobinhoodTradingService` with a `JsonApprovalStore`, create its record after broker review, and mark it approved only after the exact approval phrase is received in Codex.

For a host that can invoke MCP tools directly, provide a runner with this shape:

```python
class Runner:
    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        ...
```

Then use:

```python
from robinhood_tools.mcp_backend import McpRobinhoodBackend

backend = McpRobinhoodBackend(Runner())
```

Important: account discovery is intentionally not defaulted through the trading endpoint. The host must supply the user-selected `account_number`, and the safe service must still enforce `agentic_allowed=true` before real placement or cancellation.
