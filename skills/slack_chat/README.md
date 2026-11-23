# slack_chat

Post to the staff Slack workspace (palmettosuperfoods.slack.com) as the **Palmetto Texas Operator**
bot — the Slack counterpart of `skills/clickup_chat` for stores whose crew chat is Slack (Houston).
Direct Web API calls, no SDK.

| Function | What |
|---|---|
| `post_message(channel, markdown)` | `chat.postMessage`; converts ClickUp-style markdown (`**bold**`, `*   ` bullets, `@everyone`) to mrkdwn |
| `open_dm(user_id)` | `conversations.open`, returns the DM channel id |
| `list_members()` | Non-bot, non-deleted members as `{id, username, email}` (same shape as ClickUp members) |
| `mention(user_id)` / `to_mrkdwn(text)` | Helpers |

Token: env `PALMETTO_SLACK_BOT_TOKEN`, else Secret Manager `store-tasks-slack-bot-token`
(`jarvis-bhaga-prod`; `bhaga-orchestrator` and `store-tasks` service accounts can read it). The app is
defined by `cloud/store_tasks_slack/slack-app-manifest.yaml`; see `cloud/store_tasks/README.md` § Slack.

The bot can only post in channels it is a member of. Private channels need `/invite @Palmetto Texas Operator`.

Used by `agents/bhaga/scripts/team_pulse.py` (`send`, `resolve_target_channel`) and
`agents/bhaga/scripts/unavailability_reminder.py`, driven by the store profile's `slack` block.
