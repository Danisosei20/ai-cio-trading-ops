# Liquid signals refresh — design

## Intent

The Liquid fleet station always failed closed with "no evidence" because
`scripts/liquid_signals.py` was a cache stub: its `--refresh` flag only printed
instructions for a Co-Invest MCP that is not available in this environment.
Make the refresh real so the station runs on sourced market intelligence.

## Approaches

1. **Agent-driven payload + validated cache writer (chosen).** An agent with a
   signal feed gathers data, writes a JSON payload, and runs
   `liquid_signals.py --from-json <payload>`. The script validates the payload
   and writes the existing cache shape. Keeps the current architecture (the
   fleet reads `outputs/liquid/latest.json`) and requires no new dependency or
   network client in the Python codebase.
2. **In-process fetcher.** Add a direct HTTP client for a signal API. Rejected:
   no licensed feed is configured here, and it would expand the desk's network
   surface and credential handling for a research-only station.
3. **Populate the cache once by hand.** Rejected: not repeatable, and it would
   silently go stale.

## Decisions

- `refresh_from_payload()` validates `positioning`, normalizes symbols (strips
  `EXCHANGE:` prefixes), and fails closed with `ValueError` when no usable
  positioning evidence remains. No cache is written on failure.
- `refresh_from_mcp()` and the existing cache shape are preserved.
- New CLI flags: `--from-json PATH`, `--account`, `--source`.
- News and leaderboard are carried as supplied; unavailable feeds are recorded
  as explicit errors, never fabricated.
- Research-only: no gate, stage registry, risk controller or order path change.

## Source used in this environment

TradingView MCP `stock_options_unusual_activity` over the screened universe;
that tool's underlying vendor is Yahoo Finance, which is why positioning
entries carry `source: yahoo_finance_options` while the cache-level
`source` is `tradingview_mcp`. Put-dominant unusual volume maps to
`long_heavy_crowd` (smart money fading longs); call-dominant maps to
`short_heavy_crowd`. The news and scanner feeds were unavailable and are
recorded as errors in the payload.
