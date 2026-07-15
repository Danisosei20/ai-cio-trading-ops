# Alpaca Paper Trading

Operational broker routing is intentionally fixed:

- `research_only`: no broker service;
- `paper_auto`: Alpaca paper API only;
- `live_approval`: Robinhood Agentic account only.

There is no automatic fallback between brokers. The Alpaca adapter accepts only
`https://paper-api.alpaca.markets` and exposes no options or Alpaca live path.

## One-time setup

1. Create an Alpaca paper account and paper API keys in the Alpaca dashboard. Paper and live keys are separate.
2. Configure the paper account balance to resemble the intended live pilot. For this project's current small
   account policy, a roughly $100 starting balance produces more useful cash-floor and position-limit tests than
   Alpaca's much larger default paper balance.
3. Inject `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` from a local secret manager or process environment, and keep
   `ALPACA_PAPER_TRADE=true`. These names also match Alpaca's official MCP server. Do not paste the keys into
   Codex, Slack, Git, logs, shell history, or support bundles.
4. Set `TRADING_MODE=paper_auto` and keep `TRADING_ENABLED=false`.
   Set `PAPER_TRADING_ENABLED=true` only when intentionally enabling autonomous paper execution.
5. Run the read-only connection check:

```bash
python3 -m robinhood_tools.cli paper-broker-health
```

The command returns only the broker, `paper` environment, connection status, account label, and masked account
number. It does not submit or cancel an order. Missing credentials, rejected credentials, account blocks, an
inactive account, or any non-paper base URL fails closed.

Treat a successful health check as environment-specific read-only evidence. Record its observation time and
only the masked account identifier in the local audit store; never commit account identifiers or credentials.
If the official market clock is closed, restrict a smoke test to connectivity, reconciliation, and safety-guard
checks. Do not create an order review or approval until all required market, membership, earnings, research,
quote, spread, and volume inputs are fresh under the configured limits.

Alpaca also publishes an official local MCP server. If it is added to Codex, run `uvx alpaca-mcp-server` with
the same keys supplied by the MCP client's secret environment, `ALPACA_PAPER_TRADE=true`, and only the toolsets
needed for account, U.S. equity, market-data, news, and corporate-action work. The repository policy still
blocks options, crypto, shorts, margin, and live Alpaca even if a broader upstream tool is exposed.

## Paper order behavior

The adapter checks the authenticated paper account and asset status before producing a local review. A paper
placement must match that review's immutable order fingerprint and the durable approval record. Alpaca receives
a stable `client_order_id` for reconciliation. Fractional/notional orders use day time in force, and unsupported
extended-hours combinations are rejected locally.

Paper fills are simulations. They do not model every live effect, including market impact, latency slippage,
queue position, price improvement, regulatory fees, or dividends. Record actual paper order states and fills,
then compare them with the decision snapshot at the 1-, 5-, and 20-day checkpoints.

## Autonomous paper policy

Autonomous paper execution removes the human approval pause only for Alpaca paper. It retains a durable internal
policy authorization, exact fingerprint, atomic execution reservation, idempotent client order ID, and broker
reconciliation. Entries are regular-session DAY limit orders from 11:35 through 15:30 ET, at the unchanged
reviewed price, with no more than $500 per order or $500 total exposure to one symbol. The one-position cap,
$50 cash floor, daily/weekly realized-loss stops, earnings blackout, cooldown, current S&P 500 membership,
freshness, score, liquidity, news, and source rules remain enforced.

Send Slack after the broker action with the exact status, size, limit price, rationale, liquidity, news/chart
checks, exit plan, policy authorization, fingerprint, and order ID. Do not open a reply monitor or ask Slack to
approve an autonomous paper action. Do not report a fill or realized profit until reconciliation proves it.

Use the read-only Alpaca market-data client for snapshots, historical bars, and news. Use TradingView only as an
optional visual cross-check; record its exchange, timeframe, data source, and observation time, and block entry
when it conflicts with the primary chart analysis.

Sources: [Alpaca paper trading](https://docs.alpaca.markets/us/docs/paper-trading),
[authentication and paper endpoint](https://docs.alpaca.markets/us/v1.1/docs/authentication-1), and
[fractional trading](https://docs.alpaca.markets/us/docs/fractional-trading).
