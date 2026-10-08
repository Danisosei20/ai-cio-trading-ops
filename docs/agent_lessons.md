# Agent Lessons (operator's log — my mistakes, written down)

Updated whenever I cause an incident. Reviewed before risky work.

## 2026-10-08 — parallel debate runs throttled the LLM key
Two simultaneous TradingAgents runs + a third produced 429s across all of
them; every ticker skipped a full session. Rule: **one debate stream at a
time**; stagger multi-ticker runs sequentially in one process. Parallelism
belongs to scheduling, not to concurrent LLM streams.

## 2026-10-08 — whitespace-only edits corrupt files
Several `edit` calls with identical old/new strings collapsed newlines and
broke Python files (approvals, database, service-adjacent). Rule: **never
issue a no-op edit**; verify with `py_compile` immediately after any edit
to these files, and prefer python-script rewriting for structural changes.

## 2026-10-07 — stale UI server after config change
Added a required config key while the desk server was running; every
endpoint failed until restart. Rule: **restart long-lived servers after
config/schema changes**, then re-verify health before reporting.

## 2026-10-07 — five UI servers polling one API key
Rate-limit flakiness traced to my own server sprawl. Rule: **one server**
per purpose; kill stale ones; cache + retry on broker reads.

## 2026-10-07 — ascending bar fetch showed January prices
Triggers page displayed months-old bars as current. Rule: recency assertions
on every market-data path (fail closed or label the vintage); cross-check
new numbers against broker marks before reporting.
