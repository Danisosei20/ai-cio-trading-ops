# Full Automation Loop

Weekday cycle (America/New_York). Every step is paper-only, gated, and logged.

| Time | Job | Command | Output |
|---|---|---|---|
| 08:45 | Review | Codex automation: portfolio + market read-only review | Slack summary |
| 10:15 | Watchdog | missed-run check | health alert only on miss |
| 10:20 | Trade | `tradingagents_paper_auto.py --screen outputs/screener/latest.json --screen-top 2` | debates + gated orders |
| :00/:15/:30/:45 | Guard | `paper_position_guard.py --execute` | stops/targets enforced |
| 18:05 | Screen | `universe_screen.py --top 20` | next morning's ranking |

Install (replace `REPLACE_WITH_*` first):

```bash
cp deploy/com.openai.ai-cio-screen.plist.example ~/Library/LaunchAgents/com.openai.ai-cio-screen.plist
cp deploy/com.openai.ai-cio-paper.plist.example ~/Library/LaunchAgents/com.openai.ai-cio-paper.plist
cp deploy/com.openai.ai-cio-guard.plist.example ~/Library/LaunchAgents/com.openai.ai-cio-guard.plist
launchctl load ~/Library/LaunchAgents/com.openai.ai-cio-screen.plist \
               ~/Library/LaunchAgents/com.openai.ai-cio-paper.plist \
               ~/Library/LaunchAgents/com.openai.ai-cio-guard.plist
```

Notes:

- `--screen-top 2` bounds LLM cost (one full debate per ticker).
- The trader skips held/cooldown symbols and non-constituents automatically.
- Outside session windows every job researches/records and never places.
- Watch everything at the desk UI (`scripts/desk_server.py --port 8100).
- Logs: `outputs/paper-trader.log`, `outputs/paper-guard.log`,
  `outputs/screener.log`, `outputs/paper/tradingagents/live.log`.
