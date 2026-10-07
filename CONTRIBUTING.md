# Contributing

Thanks for stopping by. This repo moves in small, reviewed, evidence-gated
changes — the same discipline the trading code follows.

## Ground rules

1. **Paper first.** No change may risk real money, weaken a gate, or widen a
   limit without owner approval in writing. Fail closed is a feature.
2. **Small phases.** One PR = one roadmap step. Big-bang PRs get closed.
3. **Tests travel with code.** New behavior needs unit tests; execution paths
   need failure tests (timeout, duplicate, stale data, kill switch).
4. **Second pair of eyes.** Execution-adjacent changes need an independent
   review before merge (human or agent, recorded in the PR).
5. **No secrets, ever.** `.env`, databases, logs, dashboards, and keys stay
   out of git. Run `python3 scripts/secret_scan.py` before pushing.

## Workflow

```bash
git checkout -b agent/<short-topic>
# ... change, test ...
python3 -m unittest discover -s tests
python3 scripts/secret_scan.py
python -m json.tool config/approval_routes.example.json > /dev/null
git push -u origin agent/<short-topic>
# open a PR against main with: what, why, tests, risk assessment
```

## What good looks like

- Follows the phase plan in `docs/implementation_roadmap.md`.
- Updates docs + skill references alongside behavior changes.
- Keeps `NO_TRADE` a successful outcome; never optimizes for trade count.
- Commit messages say what and why, not just what.
