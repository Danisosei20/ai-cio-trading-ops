# AI-CIO Trading Operations Roadmap

Last reviewed: 2026-07-13

This roadmap tracks the path from a safety-gated prototype to a dependable AI-CIO trading operation. A checked item means repository code, tests, configuration, or CI provides evidence of completion. Connector-dependent and market-dependent work remains unchecked until it has been demonstrated end to end.

## Status key

- [x] Complete and evidenced
- [ ] Not complete or not yet evidenced

## 1. Safety foundation

- [x] Restrict new purchases to recently verified S&P 500 constituents.
- [x] Require current quotes, research sources, liquidity, volume, volatility, trend, and event-risk inputs.
- [x] Enforce account eligibility, portfolio limits, cash reserves, order-size limits, loss limits, and earnings blackout rules.
- [x] Persist expiring approvals and exact order fingerprints.
- [x] Prevent approval reuse, changed-order execution, and concurrent duplicate placement.
- [x] Default live trading to disabled and provide a durable emergency stop.
- [x] Separate research, paper, and live operating modes and data paths.
- [x] Fail closed after uncertain broker transport failures until reconciliation.
- [x] Fail closed on unexplained broker position, order, fill, dividend, or corporate-action drift.
- [x] Protect the cash floor with settled cash after unsettled funds and pending-order commitments.
- [x] Keep secrets and personal routing values out of Git.

## 2. One-task trade lifecycle

- [x] Model one durable lifecycle per ticker.
- [x] Block duplicate active lifecycle creation.
- [x] Persist research, approval, fill, exit-plan, reconciliation, and learning state.
- [x] Require broker preview and matching Codex approval for every live buy, trim, sale, or cancellation.
- [x] Treat Slack as notification and sizing input—not execution authority.
- [x] Parse and deduplicate event-scoped Slack replies safely.
- [x] Support partial-fill reconciliation with stable fill IDs and weighted prices.
- [x] Calculate realized proceeds, allocated cost basis, fees, and profit/loss.
- [ ] Demonstrate the complete lifecycle with live Slack and Robinhood connectors in one ticker task.
- [ ] Demonstrate automatic task recovery after a forced restart.

## 3. Slack operations

- [x] Configure separate trading and health Slack routes through local environment variables.
- [x] Render structured approval messages with quote, sizing, research, risk, and approval details.
- [x] Record Slack delivery attempts, retries, failures, and message identifiers.
- [x] Provide a non-posting configuration and capability health check.
- [x] Implement safe `YES`, `NO`, dollar-size, share-size, timeout, and blocked-execution parsing.
- [x] Provide a fixed-channel Slack Web API adapter for exact-thread reads and acknowledgements.
- [x] Run and record an end-to-end health-channel test message.
- [x] Verify the same task detects a real Slack reply without the operator asking Codex to check.
- [ ] Verify timeout, duplicate reply, connector failure, and task-restart behavior with the real Slack connector.

## 4. Paper-trading proof

- [x] Provide a connector-free paper broker with simulated reviews and fills.
- [x] Test approval expiry, tampering, duplicate execution, fills, loss cooldowns, and safety limits.
- [x] Record at most one isolated shadow-equity candidate or no-action observation per daily run.
- [ ] Complete at least 20 paper recommendations across different market regimes.
- [ ] Complete at least 10 closed paper trades with measured outcomes.
- [ ] Exercise qualifying trade, no-action, rejection, insufficient funds, partial fill, timeout, loss, hold-profit, trim, and sale scenarios.
- [ ] Run at least one paper lifecycle on an official open market day using current data.
- [ ] Produce a paper-readiness report before enabling any live pilot.

## 5. Research and strategy quality

- [x] Score business quality, growth, financial strength, valuation, market confirmation, execution risk, and portfolio fit.
- [x] Require a thesis, counterargument, probability, catalysts, risk/reward, and invalidation condition.
- [x] Preserve reproducible market snapshots and source evidence.
- [x] Persist content-addressed decision provenance with model, prompt, policy, snapshot, and evidence timestamps.
- [x] Require provider identity, effective time, observation time, and content hash for decision evidence.
- [x] Reject replay evidence that was not observable and effective at the original decision time.
- [x] Record strategy experiments and paper runs with replay, code, policy, parameter, dataset, artifact, and metric provenance.
- [x] Gate research acceptance on at least 10 observations and recorded human review without changing live authority.
- [x] Persist source-specific freshness manifests that identify stale and missing inputs.
- [x] Require at least 10 comparable observations before changing durable policy.
- [ ] Add sector-benchmark returns and factor/correlation exposure to outcome analysis.
- [ ] Calibrate recommendation probabilities against observed results.
- [ ] Compare entry and exit decisions with a simple S&P 500 buy-and-hold baseline.
- [ ] Replay every proposed strategy change on historical stored snapshots before paper adoption.
- [ ] Version each adopted policy change with evidence, expected benefit, and rollback criteria.

## 6. Exit and profit logic

- [x] Prevent profit alone from triggering a sale.
- [x] Evaluate thesis status, valuation, concentration, targets, peak retracement, and event risk.
- [x] Require a new broker review and explicit matching approval before any live exit.
- [x] Notify Slack only after broker state supports the reported result.
- [ ] Add sector-relative opportunity cost to exit ranking.
- [ ] Add configurable tax-impact thresholds and holding-period sensitivity.
- [ ] Validate exit decisions across at least 10 closed paper trades.
- [ ] Measure maximum favorable/adverse excursion for entry and exit calibration.

## 7. Learning and reporting

- [x] Maintain a full schema-validated CSV learning helper.
- [x] Record 1-, 5-, and 20-trading-day learning checkpoints.
- [x] Calculate benchmark-relative outcomes, thesis accuracy, and execution slippage fields.
- [x] Produce monthly performance metrics and checksummed audit exports.
- [x] Require README, roadmap, skill, helper, and test synchronization for durable workflow changes.
- [ ] Populate all learning checkpoints using real paper outcomes.
- [ ] Add automatic overdue-checkpoint processing through the connected host.
- [ ] Produce and review the first complete monthly operating report.
- [ ] Establish a quarterly strategy review with documented keep/change/revert decisions.

## 8. Reliability and operator experience

- [x] Provide database integrity checks, backups, migrations, and privacy-safe support bundles.
- [x] Provide structured redacted logs and a read-only dashboard.
- [x] Prevent duplicate daily runs with idempotency keys.
- [x] Persist daily screen checkpoints and provide a restart recovery plan for Slack, reconciliation, and stale runs.
- [x] Provide and healthy-state test an independent Keychain-backed missed-run launchd watchdog.
- [x] Verify the installed watchdog's real Keychain-to-Slack health route with a labeled non-trading test.
- [x] Render action-first notices with daily changes, data timestamps, and monitoring-only watchlists.
- [x] Provide market-calendar boundaries and trading-day calculations.
- [x] Run tests, lint, type checking, dependency audit, configuration validation, and secret scanning in CI.
- [x] Pin and enforce the direct CI toolchain versions, audited transitive security floors, and dependency updates.
- [x] Provide automated, non-destructive restore verification for integrity, schema, and required tables.
- [x] Add local operational severity checks for reconciliation, drift, stale runs, delivery, health, and learning failures.
- [x] Reject unknown, missing, or stale runtime configuration fields and schema-version drift.
- [ ] Pin all transitive dependencies with hashes in a reproducible lockfile.
- [ ] Perform and document a clean-machine restore drill.
- [ ] Perform and document an encrypted-backup restoration drill.
- [ ] Demonstrate real health-route alerts for a missed scheduled run, stale data, failed reconciliation, and overdue learning checkpoints.
- [x] Add an operator runbook covering startup, shutdown, emergency stop, recovery, and common failures.
- [x] Upgrade GitHub Actions to Node.js 24-compatible major releases.
- [ ] Repair or remove the unreliable third-party `Continuous AI: Test` status check.

## 9. Limited live pilot

Do not start this phase until the paper-readiness report is approved.

- [ ] Verify the selected account is explicitly agentic-enabled.
- [ ] Test emergency stop and broker reconciliation immediately before launch.
- [ ] Limit the pilot to one position and no more than $10–$25 per order.
- [ ] Require explicit Codex approval for every purchase and exit.
- [ ] Run the pilot for at least 30 calendar days without increasing limits.
- [ ] Review every fill, rejection, cancellation, Slack delivery, and reconciliation event.
- [ ] Complete a formal go/no-go review before increasing capital or automation.

## Recommended next sequence

1. Validate real Slack delivery and automatic reply detection.
2. Run one current-data, open-market paper lifecycle from research through exit notification.
3. Run controlled end-to-end health alerts for missed-run, stale-data, reconciliation, and overdue-checkpoint failures.
4. Test forced restart, timeout, partial-fill, and reconciliation recovery with the real connectors.
5. Perform and document clean-machine and encrypted-backup restore drills using the verification tool.
6. Collect 20 paper recommendations and at least 10 closed outcomes, including shadow observations.
7. Review calibration, benchmark-relative results, exits, and failure patterns.
8. Approve or reject a tightly limited live pilot based on documented evidence.

## Updating this roadmap

Update this file whenever durable functionality or operational evidence changes. Check an item only when a test, CI result, audit record, report, or documented live/paper exercise supports it. In the same change, update the README and installed `ai-cio-portfolio-manager` skill when the behavior affects operators or future AI-CIO tasks.
