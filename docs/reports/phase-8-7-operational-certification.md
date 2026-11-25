# Phase 8.7 — Operational Certification, Strategy Consistency & Champion-Equivalent Research Performance

Verified 2026-11-25 ET. Engineering verdict: **PHASE 8.7 VERIFIED WITH
RESTRICTIONS**. This is not unattended-trading certification and grants no
live-trading permission.

## A. Executive summary

Phase 8.7 hardened every Windows launcher, added a bounded champion-equivalent
NVIDIA NIM research benchmark with a frozen configuration and terminal failure
gating, resolved the six scout/factory disagreements as a documented
definitional difference, specified the canonical long/short rules, tightened
flatten ownership and failure semantics, certified overnight dry-run behavior,
extended the daily rehearsal to eight failure scenarios, and added read-only
dashboard visibility for scheduler, research, strategy and broker evidence.
All 1,425 automated tests pass (2 skipped); Ruff, Mypy, secret scan, execution
bypass guard and the 67-check certification harness are green. Two independent
adversarial review rounds were performed; all CRITICAL and HIGH findings were
fixed and re-verified.

Restrictions: registered tasks still execute the live checkout, so actual
scheduled-run verification is unavailable; champion-equivalent latency has
only three samples; the six strategy disagreements have no replay/out-of-sample
validation; no operational activation or strategy promotion occurred.

## B. Repository baseline and preservation

- `origin/main` = `a075e28` (PR #82, Phase 8.6), worktree branch
  `feat/phase8-7-certification` created from that exact commit.
- Owner staged files (`README.md`, `robinhood_tools/market_prices.py`,
  `scripts/desk_autopilot.py`, `scripts/options_time_machine.py`,
  `scripts/scenario_test.py`, five test modules) and all untracked
  `docs/superpowers/...` files in the main checkout were untouched.
- ADR 0001 and ADR 0002 unchanged. NVIDIA NIM remains the only research
  provider; no OpenAI integration; authorization enforcement OFF; overnight
  entries dry-run; VIX/RISK_OFF gates unchanged; no strategy promotion.

## C. Windows scheduler certification

Inventory in `docs/certification/phase-8-7-execution-inventory.md`. Ten
registered tasks plus one HKCU Run viewer were traced: executable, arguments,
cwd, interpreter, parent/child tree, run id, mode, timeout, log, artifact,
completion condition and rc propagation. All launchers now guard `cd /d` and
capture/propagate child exit codes; `office_server.bat` retains its
idempotence checks. Task Scheduler Operational log is disabled and was not
enabled. **Certification is of the worktree launcher definitions; actual
scheduled runs were not verified because the registered commands point at the
live checkout.** Owner action: deploy hardened launchers and update scheduler
commands.

## D. Intraday wrapper verification

`scripts/intraday_loop.bat` propagates loop, protective-close and verifier rcs;
a do-not-edit warning documents the byte-offset rewrite hazard.
`scripts/workflow_runner.py` records run id, UTC timestamps, child rc, redacted
output, interpreter identity, artifact postconditions and an HMAC manifest;
timeouts use process-group cleanup. `scripts/intraday_loop.py` now writes a
real `research` manifest on judge success, failure, timeout and exception, and
returns nonzero when any judge failed — previously a 3600 s timeout was logged
and the process still exited 0. Fault classes exercised in isolation: child
success, nonzero exit, missing interpreter, wrong venv, missing cwd, timeout,
parent termination, script replacement, duplicate run, missing/stale/future
artifact and manifest tampering.

## E. Champion-equivalent research configuration

Operational champion (live): `market` + `news` analysts; 4 tool rounds in the
intraday judge (script default 10); 1 debate round; 1 risk round; quick=deep=
`nvidia/nemotron-3.5-lightning-30b-a3b`; 90 s provider timeout; 1 retry; 1200 s
per ticker; 3600 s subprocess; memory log in `outputs/learning`; `save_reports`
and downstream desk gates. Reduced Phase 8.6 config: `market` only, 1 tool
round, isolated memory, no report tree.

`scripts/champion_equiv_benchmark.py` freezes the champion graph settings
(market+news, 4 rounds, 1/1 debate/risk, NIM, 90 s, 1 retry), pins all
`TRADINGAGENTS_*` overrides, computes a config digest and refuses `status=ok`
if the runtime config mismatches, the child reports an error, or analyst
reports are missing. Outputs are confined to `outputs/shadow/research/<run_id>`;
champion paths and symlinks are rejected. No broker, authorization, order,
champion artifact or `save_reports` write occurs. The benchmark is explicitly
an isolated graph: it omits the live retry re-roll, shared-memory settlement
context and downstream desk gates.

## F. Full-graph latency measurements

| Run | Config | Wall (s) | Graph (s) | Decision | Evidence complete | Config match |
|---|---|---|---|---|---|---|
| Phase 8.6 `fg-20261124-v2` | reduced (market, 1 round) | 215.666 | 208.917 | Underweight | yes | n/a |
| 8.7 `champ-equiv-20261125-v1` | champion | 252.714 | — | Sell | yes | pre-alignment run |
| 8.7 `champ-equiv-20261125-v2` | champion | 247.821 | 243.947 | Sell | yes | true |
| 8.7 `champ-equiv-20261125-v3` | champion | 354.671 | 345.982 | Underweight | yes | true |

Two gated champion samples give median ≈ 301.2 s and range 247.8–354.7 s.
**P95 is not established** with n=2; all three runs are reported for
transparency. LLM calls 18/15 and provider errors 0 in the gated runs; the
callback recorded `tool_calls=0`, so tool-call counts are NOT VERIFIED despite
4 configured rounds. Decisions differed between runs, so no deterministic
decision claim is made. With a 15:15 ET no-new-entries cutoff and 15:45 flatten,
a ~248–355 s graph must start before roughly 15:09–15:10 ET to be actionable;
this is an inference from measured latency, not a new policy.

## G. Scout/factory disagreement resolution

The six immutable November 24 snapshots contain three unique bar sets
duplicated across cycle labels, all post-close. In every case the scout's
`long_reclaim` required only `price > VWAP and price >= OR_low`, while the
canonical shadow rule also required `price > OR_high`. `scripts/scout_factory_compare.py`
now reports `intentional_strategy_difference` with the precise boundary reason
instead of `implementation_defect`, includes the spec version, and labels the
source evidence post-close. No live scout rule was changed and no factory gate
was loosened. Replay/out-of-sample validation remains outstanding.

## H. Canonical long/short specification

`docs/specs/intraday-setups/v1.0.0/SPEC.md` documents the four currently
implemented rules (scout/canonical × long/short), their VWAP and opening-range
predicates, stops/targets at OR boundaries with 2R targets and >= 1.5 R:R,
0.25% risk per trade, max 2 positions, 10:15–15:45 session, flat by 15:45,
RISK_OFF/VIX gates, HMAC evidence contract, and change control. It explicitly
records the `long_reclaim` naming collision and that no promotion is implied.

## I. Broker-owned flatten verification

`scripts/flatten_verified.py` is read-only and fail-closed. `VERIFIED` requires
a wholly flat broker account; `NO_ACTION_REQUIRED` requires every day-trade
symbol recorded in dated `auto.json` to be flat with no working order; any
unattributed equity position — even when some day-trade records exist —
produces `RECONCILIATION_REQUIRED`. Shorts are detected by negative qty and
require buy-to-cover; options remain in their own lane; overnight symbols are
excluded only when attested in `overnight.json`. Stale snapshots, unknown
order states, corrupt/missing ownership, broker outages and response-shape
anomalies all fail closed.

`scripts/flatten_daytrades.py` still performs protective closes first, now
derives the close side from position direction, refuses duplicate working
closes, and returns nonzero on any refusal, rejection or exception via a
testable `close_plan()` helper. Both launchers propagate its rc.

## J. Overnight dry-run verification

The Windows/macOS launchers keep `--dry-run` hardcoded. `overnight_pilot.py`
uses fail-closed `_spy_closes()`, rejects missing/insufficient and stale/future
bars with explicit reasons, reports the unsupported `overnight_gap` setup
explicitly, and labels every output with `execution_mode`. Order counters now
reflect actual placements (`orders_submitted`, `orders_requested`,
`exit_orders_submitted`) instead of a hardcoded zero; the non-dry-run path is
labeled `paper_order_path_authorization_off` rather than "disabled". All
candidates remained blocked in the controlled dry-run, so no overnight entry
was enabled.

## K. Daily lifecycle rehearsal

`scripts/daily_reliability_rehearsal.py` covers 10:35/09:35 overnight exit,
09:45 research and gate, 09:50 watchdog, 10:20 validate-only authorization
handoff, intraday loop, 15:10 options watch, 15:40 overnight dry-run, 15:45
flatten, 16:10 news, 16:30 shadow scoring and Friday weekly review. Eight
scenarios passed: nominal, research timeout, broker failure, data failure,
scheduler failure, duplicate workflow, weekly review and authorization
boundary. Broker outcomes are explicitly labeled simulated; zero orders; no
production scheduler permissions changed. The simulation is not a substitute
for actual scheduled-run verification.

## L. Authorization boundary tests

The 09:45 gate → decision evidence → parent authorization → 10:20
non-consuming validation → child derivation → broker boundary chain was
traced. Operational enforcement remains OFF; rehearsals use validate-only and
mock adapters. Isolated tests cover replay rejection, expiry, digest/symbol/
side mismatch and terminal-state checks without enabling enforcement in
production. No unsigned, expired or replayed authorization was accepted in
the isolated checks; no unattended trading path was activated.

## M. Dashboard changes

`/api/operational-reliability` is still loopback-only and read-only. It now
reports scheduler inventory and logging coverage, the deployment warning,
per-task parent/child evidence, champion and reduced graph duration, config
digest/match, provider errors and evidence completeness, the canonical spec
version, scout/factory counts, authorization mode, flatten scope, overnight
execution mode and explicit labels distinguishing simulated, laboratory,
shadow, paper and live evidence. Missing manifests show "data unavailable".
No `do_POST`; all interpolated values are escaped.

## N. Automated tests and CI

| Check | Result |
|---|---|
| Full tests | **1,425 OK, 2 skipped** (baseline 1,386 / 2) |
| Ruff | All checks passed |
| Mypy | Success: 93 source files |
| Secret scan | clean |
| Execution bypass guard | PASS (31 allowlisted modules) |
| Certification harness | 67 PASS, 0 FAIL, 0 WARN, 2 SKIP |

New tests cover Windows rc propagation and cwd guards, interpreter validation,
artifact freshness, HMAC/replay, champion configuration and failure gating,
research-failure manifests, strategy boundary rules, flatten ownership/shorts/
stale/unknown/duplicate cases, overnight labeling, eight rehearsal scenarios,
dashboard labels and no unauthorized order paths.

## O. Independent adversarial review

Round 1 found CRITICAL: unverified flatten ownership, refused closes returning
success; HIGH: benchmark false success, overnight order mislabeling, synthetic
research timeout, self-referential config claim, overstated launcher
certification. All were fixed. Round 2 found one remaining CRITICAL (unreachable
failure appends) and one HIGH (partial-ownership unattribution); both were
fixed through the testable `close_plan()` helper and unconditional
unattributed-equity rejection. Final reviewer verdict: **Approved**. Remaining
MEDIUM/LOW: live deployment gap, n=2 latency sample, callback tool-call count
of zero, manifest last-write-wins, snapshot-based duplicate-close check.

## P. Certification matrix

| Capability | Scenario / evidence source | Environment | Result | Remaining risk | Owner action |
|---|---|---|---|---|---|
| Windows launchers | all .bat rc/cwd guards, file tests | worktree / unit | Integration verified | live checkout not deployed | deploy launchers |
| Intraday wrapper | rc capture + research manifest tests | isolated | Integration verified | byte-offset rewrite still possible | atomic replace procedure |
| Scheduler task reporting | manifest + lifecycle precedence | isolated rehearsal | Isolated rehearsal verified | Operational log disabled | enable logging if desired |
| Full research graph | 2 gated champion runs, artifacts | real NVIDIA NIM, shadow | Integration verified | n=2, no P95, tool_calls=0 | more samples |
| Signal freshness | manifest artifact age, `signal_freshness.py` | unit | Unit verified | 15:15 cutoff not enforced in judge path | policy decision |
| Long strategy consistency | six snapshots + spec | shadow analysis | Disagreements explained | no replay/OOS | validate before promotion |
| Short strategy consistency | OR-low boundary fixtures | unit/shadow | Unit verified | same validation gap | validate before promotion |
| Market-data capability | stale/fresh fixtures, `_spy_closes` | isolated | Unit verified | live feed outages | monitor |
| Broker reconciliation | read-only Alpaca adapter mocks | simulated | Unit verified | no real broker scenario in this phase | none |
| Flatten workflow | 15 scenarios incl. partial/unknown | mocked broker | Unit verified | duplicate close race snapshot-based | none |
| Overnight dry-run | failure cases + exit-code tests | isolated | Isolated rehearsal verified | non-dry-run code path unchanged | keep dry-run flag |
| Authorization propagation | validate-only, replay/expiry tests | isolated | Unit verified | enforcement OFF operationally | owner decision |
| No-order enforcement | source guards + rehearsal invariants | isolated | Unit verified | none known | none |
| End-to-end daily lifecycle | 8 rehearsal scenarios | isolated | Isolated rehearsal verified | not actual scheduled runs | deploy + observe |
| Dashboard truthfulness | payload tests, labels, read-only route | unit | Unit verified | deployment warning remains | deploy |

## Q. Git changes, PR and merge status

Branch `feat/phase8-7-certification` from `a075e28`; changes include hardened
launchers, runner/benchmark/rehearsal modules, flatten and overnight contracts,
comparison fix and spec, dashboard extension, new tests, this report and the
execution inventory. PR and CI status are recorded at submission; no merge
without green CI and normal approval requirements.

## R. Remaining restrictions and owner decisions

1. Deploy the hardened launchers by updating scheduler commands; this phase did
   not modify production scheduler configuration.
2. Enable Task Scheduler Operational logging only with owner authorization.
3. Collect enough champion-equivalent samples for median/P95 and resolve the
   zero tool-call instrumentation before performance claims.
4. Run replay/out-of-sample validation for the canonical long/short rules; no
   live scout change or factory loosening was made.
5. Keep enforcement OFF, overnight dry-run, no unattended/live trading and no
   challenger promotion.

## S. Final readiness verdict

**PHASE 8.7 VERIFIED WITH RESTRICTIONS.**

Scheduled-workflow code, evidence integrity, flatten safety, overnight
dry-run, strategy discrepancy classification and bounded champion-equivalent
research are implemented and tested. Actual scheduled-run certification,
statistical latency certification and live strategy eligibility remain out of
scope pending the owner actions above. This verdict is an engineering
assessment, not trading authorization.
