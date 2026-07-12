# Slack Required Tools

Slack mobile push approvals require the Slack connector in the Codex host.

Required tools for direct Slack approval notifications:

- `slack._slack_send_message`

Useful fallback tools:

- `slack._slack_send_message_draft`
- `slack._slack_schedule_message`
- `slack._slack_search_channels`

## Destination

Set `channels.slack.channel_id` in `config/approval_routes.json`.

Preferred destination is a DM with the user so the Slack mobile app sends a push notification.

For a fixed, validated channel ID, user search and conversation creation are not required. Add them only when resolving a new destination.

If the channel ID is empty or Slack tools are unavailable in the same CIO task, fail closed:

- Do not treat Slack as an active approval channel.
- Stop before Robinhood placement or cancellation.

## Tool Exposure Note

Slack tools may be available only in tasks that explicitly attach the Slack plugin, for example with:

```text
[@slack](plugin://slack@openai-curated-remote)
```

The daily CIO task should include the Slack plugin mention in its own initial prompt. Do not split approval notification into a second task. If a task can read this file or the Slack skill but `tool_search` does not expose Slack send/search tools, the task should fail closed and report that Slack is unavailable in this task.

Use the channel ID supplied through `SLACK_CHANNEL_ID`; never commit a workspace-specific destination.

## Approval

Slack delivery is notification only unless a future Slack reply-reading workflow is implemented and validated.

For now, Slack approval messages should instruct the user to approve in Codex with the matching approval ID.
Record each delivery attempt, channel, Slack timestamp/link, retry count, and error. A delivery failure must never relax the placement gate.
