# Phase 8.9 — Deployment Verification

**Status: DEPLOYMENT_READY_AWAITING_OWNER.** No operational file, scheduled task,
host logging setting or owner work was modified. The live checkout remains at
`a991398`; the reviewed worktree is at `1c5dc8c`.

## 1. Target and authorization

- Target: `C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops`.
- All 10 registered `AI-*` tasks execute `.bat` files in that checkout via 8.3
  short paths; `Arguments` and `WorkingDirectory` are empty, run-as `Danis Akoto`,
  `InteractiveToken`, `IgnoreNew`, no explicit execution time limit.
- Owner authorization for deployment is **absent**: the Phase 8.8 plan records
  `owner_authorized: false`, `applied: false`, and ADR 0002 approval is design
  scope only. No approval flag exists in the repository or environment (names
  checked; no secret values read).
- The current session also lacks write permission on the OneDrive checkout
  (`CodexSandboxUsers: ReadAndExecute`). No copy was attempted.

## 2. Parity (recalculated this phase)

- 26 allowlist paths = 24 deployable components + 2 ADRs.
- **24 DEPLOYMENT_REQUIRED, 2 MATCH (ADRs), 0 CONFLICT, 0 NOT_VERIFIED.**
- No destination local modifications on any allowlist path; owner staged and
  untracked files are outside the allowlist and intact.
- Six source hashes changed since Phase 8.8 because of Phase 8.9 work
  (`flatten_daytrades.py`, `overnight_pilot.py`, `operational_reliability.py`,
  `daily_reliability_rehearsal.py`, `champion_equiv_benchmark.py`,
  `web/agent-office/trading.html`). The allowlist itself is unchanged and exact.

## 3. Deployment helper verification

`scripts/deploy_hardened_launchers.py` provides: dry-run default; exact allowlist;
source/destination hash checks; conflict and unavailable-source blocking;
09:45–16:05 ET active-window guard; running-task guard; hash-verified backup
manifest written incrementally before each replacement; created-file rollback;
atomic temp+replace; post-deployment hash verification; and no secret or
scheduler changes.

Rollback rehearsals in isolated temp roots cover: successful deploy and rollback;
partial failure with a persisted backup manifest and restoration; conflict
blocking; running-task refusal; active-window refusal; created-file removal;
duplicate-key rejection; and permission failure. Failed post-verification and
interrupted-copy paths are exercised through the partial-failure harness.

## 4. Pre-deployment checks performed

Target path confirmed; main commit confirmed; all source/destination hashes
recomputed; destination modification state checked; active tasks read-only;
no AI task running; venvs verified (both Python 3.11.9, required packages by
name); disk space 674 GB free; dry-run produced 24 COPY / 2 SKIP / 0 CONFLICT;
rollback package tested in isolation.

## 5. Not performed

- No deployment applied.
- No scheduler task registered, triggered, disabled or edited.
- No host-wide logging enabled.
- No production post-deployment verification possible; therefore no
  `ACTUAL_SCHEDULER_VERIFIED` claim is made.

## 6. Owner actions required

1. Authorize the 24-file deployment in writing for a named maintenance window.
2. Confirm the rollback backup location and retention.
3. After deployment, recompute hashes, re-check interpreter paths and run
   non-trading preflight checks; only then treat scheduler results as evidence.
