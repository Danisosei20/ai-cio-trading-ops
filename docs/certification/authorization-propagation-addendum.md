# Addendum — Authorization ID Propagation (Item 1 complete)

Date: 2026-11-23. ADR 0002 approved (design scope); enforcement flag still OFF.

## Flow implemented

`09:45 gate → parent authorization → 10:20 validate (no consume) → parent id
→ --authorization-id → per-order child authorization → SignalOrder → gateway →
broker`. Exits retain their authorization-free path.

- `scripts/execution_handoff.py:parent_id_for_run` exposes the validated parent id.
- `scripts/intraday_loop.py` passes it into `run_judge` as `--authorization-id`.
- `scripts/tradingagents_paper_auto.py` derives a child for buy entries and
  forwards the parent to option openers; sell exits are untouched.
- `scripts/daytrade_engine.py` threads the parent through `run_cycle` and
  derives children for both long and short entries; `_exit_position` is unchanged.
- `robinhood_tools/options_flow.py:place_long_option` validates and consumes a
  child for option openers; `close_long_option` is unchanged.
- `robinhood_tools/paper_flow.py` now gates on `position_effect`, not `side`,
  so short opens (sell/open) are enforced and buy-to-cover (buy/close) stays free.
  It also passes `environment="paper"` and the real repo root to the gateway.

## Why child authorizations

The 09:45 parent binds the decision, evidence and account for the trading
session (8-hour default); each child is short-lived (5 minutes);
one intraday cycle places several orders. A signed, short-lived child inherits
the parent's decision identity and evidence hashes while binding the actual
symbol, side, quantity and exposure. The child is idempotent by construction and
atomically consumed once at the broker boundary.

## Verified

- 1,073 tests OK (2 skipped), ruff clean.
- Controlled verification with enforcement ON and a mocked broker:
  long entry, short entry and option entry each authorized and replay-blocked;
  both exit directions allowed; tampered parent rejected.
- Regression tests cover concurrency (single winner), partial-fill retries,
  restart/SQLite durability, expiry, tampering and no-LLM assertions.

## Adversarial review fixes

An independent review found three blocking defects, all fixed and retested:
the parent TTL was too short for a 10:20 execution (now 8 hours); the
day-trade engine never received the parent id (now wired in `main()`); and exit
orders defaulted to `position_effect="open"` and would have been blocked — all
exit producers now mark `position_effect="close"`. Also fixed: children cannot
exceed parent limits or a 20-child cap, and the gateway reads account/strategy
from the child authorization rather than placeholders.

## Still required for unattended certification

HMAC key hardening (rotation/key-id/permissions), a full 10:20 rehearsal with
the flag enabled, and convergence of legacy kill switches. The flag remains OFF.
