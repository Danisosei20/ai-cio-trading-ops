# Upstream Architecture Review

Reviewed: 2026-07-13

This review compares the equity-only AI CIO with established open-source investment and trading systems. The
goal is to adopt small, auditable safety patterns—not to import a large trading engine or expand execution
authority. No upstream source code was copied.

## Patterns adopted

### Microsoft Qlib

Qlib separates experiments from individual runs and records parameters, metrics, and artifacts. It also treats
point-in-time data as essential because later revisions can leak future information into historical analysis.

Applied here:

- immutable research experiments with hypothesis, baseline, strategy/code/policy/parameter versions,
  expected benefit, rollback criteria, and replay digest;
- content-addressed paper runs with dataset and artifact hashes plus excess return, drawdown, turnover, and
  slippage metrics;
- a minimum ten-observation and recorded-human-review gate before research acceptance;
- provider, effective time, observation time, and content hash on every decision evidence item.

Sources: [Qlib Recorder](https://github.com/microsoft/qlib/blob/main/docs/component/recorder.rst) and
[Qlib point-in-time database](https://github.com/microsoft/qlib/blob/main/docs/advanced/PIT.rst).

### NautilusTrader

NautilusTrader emphasizes deterministic research/live semantics, execution reconciliation, and fail-fast
configuration. This repository already had durable order/fill reconciliation and deterministic replay guards.

Applied here:

- startup now rejects unknown, misspelled, missing, or stale configuration fields;
- the configured database schema version must exactly match the code schema;
- research acceptance remains separate from live order authority.

Source: [NautilusTrader repository and releases](https://github.com/nautechsystems/nautilus_trader).

## Patterns already present

### QuantConnect LEAN

LEAN provides composable risk models including per-security drawdown, portfolio drawdown, and maximum sector
exposure. This project already enforces position weight, sector weight, cash, order size, open-position count,
loss stops, liquidity, and event controls. The small account does not justify importing LEAN's engine.

Source: [LEAN risk models](https://github.com/QuantConnect/Lean/tree/master/Algorithm.Framework/Risk).

### OpenBB

OpenBB standardizes interchangeable data-provider extensions and provider priority lists. This repository
already keeps data and broker integrations behind host protocols, so provider packages remain outside the
credential-free core. Provider identity is now mandatory in decision evidence. Automatic fallback remains
deferred until two real providers are connected and discrepancy behavior is tested.

Source: [OpenBB provider extensions](https://docs.openbb.co/odp/python/extensions/providers).

## Deliberately not adopted

- reinforcement learning, high-frequency execution, or automated live strategy deployment;
- options, margin, short selling, leveraged/inverse products, or broader asset classes;
- a second full event engine or a large ML dependency stack;
- silent provider fallback, because it can hide degraded or inconsistent data;
- automatic strategy promotion or capital-limit increases.

## Remaining high-value gaps

1. Collect 20 paper recommendations and 10 closed paper trades across regimes.
2. Build the full historical point-in-time dataset catalog and replay runner; current code validates evidence
   envelopes and research promotion but does not manufacture clean historical data.
3. Add sector benchmark, factor, correlation, and opportunity-cost attribution.
4. Calibrate recommendation probabilities and compare decisions with a simple S&P 500 baseline after costs.
5. Run real connector restart, timeout, reconciliation, health-route, clean-machine, and encrypted-restore
   drills.
6. Generate a fully hashed transitive dependency lock and repair or remove the unreliable third-party CI check.

These gaps should be closed with paper and operational evidence before any live limit is increased.
