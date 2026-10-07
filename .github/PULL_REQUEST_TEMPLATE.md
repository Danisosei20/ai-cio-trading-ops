## What is this PR changing and why?

## Evidence (tests, backtests, paper runs)

- [ ] `python3 -m unittest discover -s tests` green
- [ ] `python3 scripts/secret_scan.py` clean
- [ ] Independent review completed (for execution-adjacent changes)

## Risk assessment

- [ ] No gate weakened, no limit widened, no live path touched
- [ ] Fails closed on missing/stale input
- [ ] No secrets, credentials, or account data included

## Checklist

- [ ] Docs updated (`docs/`, README, skill references as needed)
- [ ] Config changes (if any) approved by the owner in writing
