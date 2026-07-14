# Repository Skills

This directory contains sanitized, version-controlled copies of Codex skills used by this project.

## AI CIO Portfolio Manager

`ai-cio-portfolio-manager/` contains the operating skill, policy references, agent metadata, and journal helper
used by the AI CIO workflow. It intentionally excludes local automation memory, account data, Slack channel IDs,
tokens, Keychain items, databases, dashboards, logs, and Python cache files.

The installed personal skill remains the runtime copy under `$CODEX_HOME/skills/ai-cio-portfolio-manager`.
When durable policy changes are adopted, update the installed skill and this repository copy together, run the
repository checks, and review the diff for secrets before publishing.

Use the deterministic allowlisted sync commands from the repository root:

```bash
make skill-check
make skill-sync-from-installed
make skill-sync-to-installed
```

`skill-check` fails on any missing or different governed file. The two sync commands copy only the documented
skill files; they never copy `.env`, automation memory, credentials, account data, routing IDs, generated output,
or Python caches. Choose the direction containing the intentional change, then run `make skill-check` before
finishing the task.

To install the repository copy into another Codex home, copy the `ai-cio-portfolio-manager` directory into that
environment's `$CODEX_HOME/skills/` directory. Review `agents/openai.yaml` first and confirm the listed Robinhood
and Slack integrations are appropriate for that environment.
