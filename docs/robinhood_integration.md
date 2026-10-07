# Robinhood Integration — read-only first, execution last

Robinhood documents external-agent trading surfaces (option chains,
quotes, positions, order review/cancel/placement) with a confirmation
model the agent host configures. Design implication used here: **review
before placement is mandatory in our code regardless of the broker's
settings**, and our deterministic risk service sits between any AI and
any order.

## Adapter shape (target)

```python
class Broker:
    get_account() / get_positions()
    get_option_chain() / get_option_quote()
    review_order()      # never places; returns review ID + estimates
    place_order()       # requires review ID + ledger authorization
    cancel_order() / get_orders() / get_fills() / close_position()
```

`SimulationBroker` (project-owned, long options + equity) and
`AlpacaPaperBroker` (existing `AlpacaPaperBackend`, equities) implement it
first. `RobinhoodBroker` implements it against the MCP/host connector
(existing `mcp_backend.py` + `client.py` protocol are the starting seam —
today they cover equities; options legs extend `OptionOrderRequest`,
which already exists as a data type).

## Staged integration (each stage is a shippable phase)

1. **Read-only** (Phase 7): account, positions, option chains, quotes,
   orders, fills. No review/place code paths. Proves credentials scoping,
   clock/quote plumbing, and chain-data quality gates.
2. **Review-only** (Phase 8): `review_option_order` wired to ledger
   authorizations that can never execute (execution policy OBSERVE).
   Proves fingerprinting + expiry + idempotency without market risk.
3. **Approval execution** (Phase 9): human clicks Approve → single submit →
   reconcile. `require_human_confirmation=True`, live kill switch owned by
   human config, Slack/Codex approval record required.
4. **Shadow autonomous** (Phase 10): pipeline runs, orders withheld,
   simulated fills scored vs proposals. Evidence gate for Phase 11.
5. **Autonomous config** (Phase 11+): allowed only after simulation +
   shadow evidence, caps proportional to evidence, human enables it
   explicitly. Never the default; never reachable from model output.

## Invariants (hold on every stage)

- Pre-trade review stored before any submit; fingerprint binds
  review ↔ authorization ↔ order.
- Idempotency keys on every submit; reconcile-before-retry on timeouts
  (a timeout is *unknown*, never *failed*).
- Cancel/close paths need the same authorization discipline as opens.
- Simulated Returns-style scenario tools inform sizing; they are not a
  ledger and never substitute for the paper environment.
- Credential scope: only the execution service process holds broker
  credentials (Kubernetes Secret / secret manager). Research, debate,
  UI, and backtest workloads receive no secrets.
