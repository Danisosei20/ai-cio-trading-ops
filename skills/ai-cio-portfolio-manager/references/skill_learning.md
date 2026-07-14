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
5. Run `quick_validate.py` on the skill folder.
6. Tell the user what durable lesson was added.
7. When the skill is paired with a repository, update its README and versioned helper scripts/tests in the same change so operating instructions do not drift.
8. Update the repository roadmap in the same change. Mark an item complete only when supported by a test, green CI result, audit record, report, or documented connector/paper/live exercise; code existence alone does not prove an external integration works.

## Evidence Standard

Track recommendations and fills at 1-, 5-, and 20-trading-day checkpoints versus the S&P 500. Record thesis accuracy, excess return, execution slippage, and whether volume, liquidity, trend, volatility, and event assumptions were correct. Do not optimize rules to one winner or loser. Require at least 10 comparable observations and a repeated error pattern before changing a weight or durable rule; document the old rule, evidence, change, and expected improvement.

Use a transactional audit store when the workflow can execute orders. Reserve an approved order atomically before placement so concurrent tasks cannot reuse it. If transport fails after placement may have reached the broker, mark the approval `reconciliation_required`; query broker order state before any retry. Preserve the market-snapshot hash, source evidence, exit plan, correlation ID, and learning checkpoints with the approval.
