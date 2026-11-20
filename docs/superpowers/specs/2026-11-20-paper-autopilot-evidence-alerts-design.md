# Paper Autopilot Evidence and Alerting Design

## Intent

Make the paper desk's autonomous options path evidence-first and operationally
quiet outside market hours. Every paper entry must be justified by a recorded
technical snapshot and must immediately produce a Slack alert; periodic checks
must not send hourly noise when the market is closed, on weekends, or on market
holidays.

## Approved Scope

- Ignore the phrase “Trojan astrology”; it is not a strategy requirement.
- Paper trading only; no live broker enablement or live order path changes.
- Long calls and puts remain supported.
- Use VWAP, EMA, POC, supply/demand, support/resistance, market regime,
  liquidity, IV, earnings, session, position, premium, and risk gates.
- Record all evidence before an entry attempt, including blocked attempts.
- Send an immediate Slack alert after every accepted paper order, with the
  timestamp, contract, reviewed price, side, thesis, policy version, and
  evidence record reference.
- Suppress periodic Slack checks when the market is closed, during weekends,
  and on exchange holidays. Exit monitoring remains available when positions
  require protection.
- Optimize for positive expectancy, controlled drawdown, and low loss severity;
  do not promise a win rate or guaranteed profits.

## Existing Boundaries

The current desk already has deterministic options screening, paper-only
execution gates, an options guard, policy metadata, and Slack notification
helpers. This work adds a thin orchestration/evidence seam rather than
rewriting the server or broker abstraction. Existing entry gates remain the
authority and exits cannot be disabled by new entry logic.

## Approaches

1. **Deterministic paper autopilot (selected):** collect indicators and facts,
   persist an immutable pre-entry record, run existing gates, execute only
   paper orders, and alert immediately. This is predictable, testable, and
   keeps LLM use out of the time-sensitive alert path.
2. **LLM-led autonomous trader:** ask an agent to interpret the full chart and
   decide entries. Rejected because it weakens reproducibility and makes
   evidence and risk enforcement harder to audit.
3. **Shadow-only research loop:** record candidates without placing paper
   orders. Useful for a later calibration phase, but does not satisfy the
   approved paper-autopilot objective by itself.

## Proposed Flow

```text
market/session check
  -> deterministic scan
  -> technical evidence snapshot
  -> freshness + policy + risk gates
  -> persist candidate before order
  -> paper order through existing gated flow
  -> persist result and alert Slack immediately
```

## Evidence Contract

Each candidate record contains:

- unique candidate ID and UTC/ET timestamps
- symbol, option OCC symbol, side, quantity, reviewed quote, and premium
- VWAP, EMA values, POC, supply/demand zones, support/resistance levels
- regime, IV, liquidity/spread, earnings date, market/session status
- policy version, gate results, final action, and reason
- order ID/status when accepted, plus Slack delivery result

Missing or stale entry evidence blocks the entry and records the reason.
Evidence storage must be atomic and must not include broker credentials.

## Alert Contract

- `paper_entry_accepted`: emitted once immediately after the gated paper
  order is accepted by the broker adapter.
- `paper_entry_blocked`: optional structured log/artifact, not a Slack alert
  unless configured for a high-severity operational failure.
- `periodic_check_skipped`: local log only for closed/weekend/holiday status.
- Slack failures are recorded and retried through the existing safe notifier;
  they never cause a duplicate order.

## Scheduling and Safety

- Session checks use the exchange calendar, not weekday-only heuristics.
- No hourly Slack message is emitted outside an open session.
- Exit/guard checks remain available independently of entry-session checks.
- Duplicate candidate/order stamps prevent re-firing the same entry.
- Every entry path remains paper-only and fail-closed on unsafe configuration.

## Approved Safety Decisions

- When the exchange is closed, exits are monitored and logged only; the
  autopilot does not queue stale exits for later submission.
- Duplicate prevention uses a full candidate fingerprint plus exact OCC
  contract identity, while retaining the existing underlying-position block.
- Accepted-order Slack alerts retry for 10 minutes with bounded backoff; a
  failed alert can never place a second order.
- SQLite is the source of truth and each candidate also gets an atomic JSON
  evidence artifact for audit/export.
- Persist screened candidates and downstream gate failures, but not every raw
  universe rejection.
- Multiple autopilot processes are possible; a database lease/claim prevents
  concurrent entry cycles.

## Verification

- Unit tests for evidence completeness, stale/missing data, session/holiday
  suppression, duplicate prevention, and immediate alert payloads.
- Integration tests prove the order is gated before alerting and that Slack
  failure cannot place a second order.
- Existing full suite and scenario matrix remain green.
- Dry-run and paper execution scenarios must show no live calls.
