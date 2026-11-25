# Phase 8.8 — Development and Operational Checkout Parity

Generated 2026-11-25 ET from a read-only comparison. No files, scheduler tasks,
virtual environments or host logging settings were changed. No secret content is
recorded.

## 1. Checkout identities

- Development worktree: `phase8-8-parity`, HEAD `f9e2b68` (PR #83), clean.
- Operational checkout: `C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops`, HEAD `a991398`, four commits behind, with owner staged and untracked changes preserved.
- Both venvs exist and report Python 3.11.9: desk `.venv` in the operational checkout and `TradingAgents\.venv`. The worktree has no venv and is a source, not an execution site.
- Ten registered `AI-*` tasks point at the **operational checkout** via 8.3 short paths, with empty `Arguments` and empty `WorkingDirectory`; `MultipleInstancesPolicy=IgnoreNew`, no explicit `ExecutionTimeLimit`, no restart settings, `InteractiveToken` run-as `DANIZPC\Danis Akoto`. Last results: IntradayLoop 255, OvernightEnter 1, all others 0.
- ADR 0001 and ADR 0002 are byte-identical in both checkouts (`MATCH`).
- Task Scheduler Operational log state was not inspected or enabled; host-wide logging changes require owner approval.

## 2. Classification rules

- **MATCH** — source and destination SHA-256 identical.
- **DEPLOYMENT_REQUIRED** — destination missing or hash differs from reviewed source; no owner-local modification detected.
- **CONFLICT** — destination file has local owner changes (staged or unstaged); never overwrite.
- **NOT_VERIFIED** — source missing or comparison could not be completed.

## 3. Parity matrix

Counts: {'DEPLOYMENT_REQUIRED': 24, 'MATCH': 2}. Full hashes are in `phase-8-8-parity-data.json`.

| Component | Classification | Reason | Source SHA-256 (first 16) | Destination SHA-256 (first 16) |
|---|---|---|---|---|
| scripts/intraday_loop.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `5996816b7eac4189` | `288986510933149c` |
| scripts/auto_trade_weekday.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `08e8bd8abccf65f2` | `e6bb4616a2671d5f` |
| scripts/flatten_1545.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `7e798f145f355807` | `0c0cba72c3865c27` |
| scripts/overnight_enter.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `2249eeeda7cd84db` | `7931efeb68d51d0c` |
| scripts/overnight_exit.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `327a4c33135f0842` | `a8c046b991b9889b` |
| scripts/options_watch.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `a43cee50fb9ed43d` | `ea0c85efd9dcc855` |
| scripts/news_daily.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `61e5200b2bf9f5e0` | `8f1b0aa838a22b02` |
| scripts/score_shadow.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `1dd6b95bef261222` | `3e1556a710fa6c1d` |
| scripts/watchdog_windows.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `706fd890b25c9d69` | `8802805be45e3a15` |
| scripts/weekly_review.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `d15dac459fd25237` | `c9eb15e9ff25d7bc` |
| scripts/daytrade_1030.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `7f245d59c5653596` | `0a5fd351c0d02fc3` |
| scripts/office_server.bat | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `d3914d46f74c2087` | `02403c0d80aa5a3b` |
| scripts/workflow_runner.py | DEPLOYMENT_REQUIRED | destination missing | `9606e68b6286e727` | `` |
| scripts/flatten_verified.py | DEPLOYMENT_REQUIRED | destination missing | `53a19a507b9047fa` | `` |
| scripts/flatten_daytrades.py | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `15f99c0e2064f695` | `719e1faf2dc69558` |
| scripts/intraday_loop.py | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `1cd74c79f3dfd7ec` | `8457ed7a40ad2bdb` |
| scripts/overnight_pilot.py | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `d1524d2d2f8a9894` | `e689807d0a08190c` |
| scripts/operational_reliability.py | DEPLOYMENT_REQUIRED | destination missing | `347c91e84f5d4e40` | `` |
| scripts/daily_reliability_rehearsal.py | DEPLOYMENT_REQUIRED | destination missing | `c3b6a08d95fb2d61` | `` |
| scripts/champion_equiv_benchmark.py | DEPLOYMENT_REQUIRED | destination missing | `714b65e97edf1b71` | `` |
| scripts/agent_office.py | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `c5f8b88222e53b91` | `54e9082512dec23d` |
| robinhood_tools/workflow_manifest.py | DEPLOYMENT_REQUIRED | destination missing | `8b145aba19c42550` | `` |
| web/agent-office/trading.html | DEPLOYMENT_REQUIRED | hash differs; reviewed source is newer | `6d772fb94895ece3` | `60517ead388ab4f7` |
| docs/specs/intraday-setups/v1.0.0/SPEC.md | DEPLOYMENT_REQUIRED | destination missing | `68f55225eec0917c` | `` |
| docs/adr/0001-deterministic-gate-ai-research-only.md | MATCH | hash identical | `bc6ff8ccdbbce770` | `bc6ff8ccdbbce770` |
| docs/adr/0002-execution-authorization-protocol.md | MATCH | hash identical | `d02079fad23377a1` | `d02079fad23377a1` |

## 4. Deployment readiness

`scripts/deploy_hardened_launchers.py` dry run produced: mode=`dry_run`,
owner_authorized=`False`, applied=`False`, active_window=`False`,
running_tasks=`[]`, secrets_touched=`false`, scheduler_modified=`false`.
Planned actions: 24 COPY, 2 SKIP, 0 CONFLICT.

Applying requires both `--apply` and `--owner-authorized`. The helper refuses
to replace batch files between 09:45 and 16:05 ET, refuses while any AI task is
running, verifies source hashes, backs up every destination file with a hash
manifest, writes via temporary file plus atomic replace, verifies post-deploy
hashes, and supports `--rollback <manifest>` with hash verification. It never
touches `.env`, owner staged/untracked files, scheduler definitions or virtual
environments.

## 5. Owner authorization status

**NOT AUTHORIZED IN THIS PHASE.** No operational file was modified and no
scheduled task was triggered or edited. The deployment package is prepared for
review only. Two live tasks already report failures (255 and 1) because the
hardened exit-code logic is not deployed; those results must not be treated as
proof that the new launchers are active.

## 6. Required owner actions

1. Review and authorize the 24-file allowlist deployment.
2. Choose a maintenance window outside 09:45–16:05 ET.
3. Approve rollback procedure and backup location.
4. Optionally enable Task Scheduler Operational logging.
5. After deployment, verify task last results and terminal manifests from the
   real operational checkout; do not accept CI as deployment evidence.
