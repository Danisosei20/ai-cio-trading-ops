# Skill Learning

Update this skill when a durable process lesson emerges. Do not update the skill for ordinary market facts, prices, account balances, or one-time recommendations.

Good reasons to update:

- A repeated workflow problem
- A new safety rule
- A better journal field or dashboard field
- A clarified Robinhood tool behavior
- A recurring user preference
- A validated improvement to scoring or review structure

Bad reasons to update:

- Today’s price movement
- A single trade outcome without broader lesson
- A temporary macro view
- A private account number or credential

When updating:

1. Edit the smallest relevant file.
2. Keep `SKILL.md` concise.
3. Put detailed policy/workflow changes in `references/`.
4. Preserve trading safety gates.
5. When the paired repository is available, synchronize the installed and repository copies in the same task. Use the repository's allowlisted `scripts/sync_ai_cio_skill.py` with `from-installed` or `to-installed`, depending on which copy contains the intentional change.
6. Run `scripts/sync_ai_cio_skill.py check` and do not finish while the governed files differ.
7. Run `quick_validate.py` on the skill folder.
8. Tell the user what durable lesson was added.
9. Update the repository README and versioned helper scripts/tests in the same change so operating instructions do not drift.
10. Update the repository roadmap in the same change. Mark an item complete only when supported by a test, green CI result, audit record, report, or documented connector/paper/live exercise; code existence alone does not prove an external integration works.

## Evidence Standard

Track recommendations and fills at 1-, 5-, and 20-trading-day checkpoints versus the S&P 500. Record thesis accuracy, excess return, execution slippage, and whether volume, liquidity, trend, volatility, and event assumptions were correct. Do not optimize rules to one winner or loser. Require at least 10 comparable observations and a repeated error pattern before changing a weight or durable rule; document the old rule, evidence, change, and expected improvement.

Use a transactional audit store when the workflow can execute orders. Reserve an approved order atomically before placement so concurrent tasks cannot reuse it. If transport fails after placement may have reached the broker, mark the approval `reconciliation_required`; query broker order state before any retry. Preserve the market-snapshot hash, source evidence, exit plan, correlation ID, and learning checkpoints with the approval.

Record immutable provenance for every recommendation or no-action decision: model and version, prompt hash,
policy version and hash, market-snapshot hash, source timestamps, score, and rationale. A decision record is
evidence only and must never create broker or execution authority. Replay a proposed strategy change using only
observations that were both known and effective at each historical decision time; reject future-dated evidence
to prevent look-ahead bias. Keep deterministic safety policy, broker review, and explicit Codex approval in
control even when model output changes.

Treat strategy changes as registered research experiments, not prompt edits. Record the hypothesis, baseline,
strategy and code version, policy/parameter hashes, point-in-time replay digest, expected benefit, rollback
criteria, dataset/artifact hashes, and cost-aware metrics. Keep runs paper-only and require at least ten
comparable observations plus recorded human review before research acceptance; acceptance never changes live
policy or creates execution authority automatically. Decision evidence must identify its provider, when the
data became effective, when it was observed, and its content hash. Reject unknown or missing configuration
fields so a typo cannot silently disable a safety control.

Keep broker environments structurally separate. Route paper mode only to a provider's dedicated paper endpoint
and live mode only to the approved live broker; reject live-looking paper URLs and never silently fall back
between brokers. Use distinct credentials and durable stores, verify paper connectivity read-only before a
session, and retain exact review fingerprints, approvals, reconciliation, and learning records in paper mode so
testing exercises the real safety workflow without creating live authority.

When a user delegates autonomous paper execution, preserve a separate default-off paper switch and never infer
live authority. Replace human approval only with a durable internal policy authorization, unchanged fingerprint,
atomic reservation, regular-session limit order, bounded per-order and per-symbol exposure, reconciliation, and
post-trade notification. Treat chart-driven capitulation rules as paper experiments until repeated outcomes
support them; never promise profitability or optimize policy from isolated wins.
