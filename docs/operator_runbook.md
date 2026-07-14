# AI CIO Operator Runbook

This runbook is for the equity-only AI CIO service. Options remain prohibited. All times are Eastern Time
(`America/New_York`). Slack can request rejection or fresh sizing but never authorizes execution.

## Start-of-day sequence

1. Keep `TRADING_ENABLED=false` unless a previously approved live pilot is active.
2. Run `python3 -m robinhood_tools.cli operations-status`. Do not continue from `critical`.
3. Run `python3 -m robinhood_tools.cli recovery-plan`. Resume unexpired exact-thread Slack monitors first,
   reconcile every uncertain broker approval second, and recover stale daily runs third.
4. Confirm the separate watchdog is loaded and its error log is empty.
5. Verify Robinhood read access, the fixed trading Slack route, and the separate health route.
6. Reconcile positions, open orders, fills, dividends, and corporate actions before new research.
7. Run the daily review only after every required source has a fresh timestamp and content hash.

An operational check is not trading authorization. A live order still needs an unchanged broker review,
matching order fingerprint, unexpired approval, and explicit matching approval in Codex.

## Normal shutdown

1. Stop starting new reviews.
2. Let an active broker call finish; never retry an uncertain placement blindly.
3. Record any uncertain result as `reconciliation_required`.
4. Preserve open Slack-window and ticker-lifecycle state for restart recovery.
5. Run `operations-status`, export the audit bundle, create a SQLite backup, and encrypt the backup.
6. Leave the emergency kill on if shutdown followed an incident.

## Emergency stop

Run:

```bash
python3 -m robinhood_tools.cli emergency-stop
```

This blocks new reviews and placements. It does not cancel orders or sell holdings. Inspect broker state
directly and reconcile it with durable state. Do not resume merely because the original error disappeared.

Resume only when database integrity is `ok`, no approval needs reconciliation, no unexplained broker drift
remains, all active orders and fills are accounted for, credentials/routes have been verified, and the operator
has documented the incident. Then run:

```bash
python3 -m robinhood_tools.cli emergency-resume
```

## Failure response

| Condition | Required response |
| --- | --- |
| Missed daily run | Keep trading disabled, inspect watchdog logs and automation memory, run the watchdog manually, then recover the daily run once. |
| Stale or missing source | Produce `No Action Recommended`; refresh the named source and rebuild the freshness manifest. |
| Broker timeout/unknown result | Mark reconciliation required, query broker order/fills, and never submit a replacement until resolved. |
| Broker-state drift | Stop recommendations; map every position, order, fill, dividend, and corporate action to lifecycle state. |
| Slack send/read failure | Do not create execution authority; use the health route, preserve the approval, and reject it at expiry. |
| Expired Slack window | Reject the linked pending approval and clean the terminal window. Any renewed interest starts a fresh review. |
| Database integrity failure | Emergency stop, preserve the files, restore the latest verified backup to a new path, and do not overwrite the source. |
| Overdue learning checkpoint | Degrade health, process the checkpoint with point-in-time benchmark data, and keep policy unchanged until evidence is complete. |
| Credential exposure | Emergency stop, revoke/rotate at the provider, remove local material, scan history, and verify fixed routes before restart. |

Health failures go only to the configured health route. Never include tokens, cookies, account numbers, or raw
connector payloads in Slack, logs, support bundles, or Git.

## Backup and restore drill

Create a consistent SQLite backup, then encrypt it outside Git:

```bash
python3 -m robinhood_tools.cli backup outputs/backups/cio.db
scripts/encrypted_backup.sh outputs/backups/cio.db outputs/backups/cio.db.enc
```

Verify an unencrypted backup or an already decrypted temporary copy without modifying it:

```bash
python3 -m scripts.restore_drill outputs/backups/cio.db
```

Set `AI_CIO_BACKUP_PASSPHRASE` through a local secret manager or private shell prompt before running the
encryption command; never commit or paste it into logs. The drill restores into a temporary directory, checks SQLite integrity, schema version, and required tables,
then confirms the source hash is unchanged. For an encrypted drill, verify the checksum, decrypt into a secure
temporary location, run the same command, and securely remove the temporary plaintext according to local policy.
Record the date, operator, backup identifier, result, and recovery time. The automated test proves the tool;
it does not replace a documented clean-machine or encrypted-backup drill.

## Release and policy changes

Before merging a release:

```bash
python3 -m unittest discover -s tests
.venv/bin/ruff check robinhood_tools scripts tests
.venv/bin/mypy robinhood_tools
.venv/bin/coverage run -m unittest discover -s tests
.venv/bin/coverage report
python3 scripts/secret_scan.py
python3 scripts/sync_ai_cio_skill.py check
```

Every decision record must preserve the model version, prompt hash, policy version/hash, market snapshot hash,
source timestamps, recommendation, score, and rationale. That provenance is evidence, not authorization.
Proposed policy changes require repeated comparable observations, a point-in-time replay with no look-ahead
data, expected benefit, rollback criteria, paper adoption, and separate approval before live use.

## Monthly and quarterly review

Monthly, review realized and unrealized performance, S&P 500-relative returns, maximum favorable/adverse
excursion, slippage, calibration, safety stops, health incidents, reconciliation time, missed runs, Slack
delivery reliability, overdue checkpoints, and restore status. Quarterly, decide explicitly to keep, change,
or revert each strategy version. Capital limits never increase automatically.
