# clickup_browser — ClickUp messages with real @mentions (Issue #435)

The ClickUp REST API (`skills/clickup_chat`) stores `@everyone` as plain text, so
it never notifies anyone and pastes as text. This skill types the message into the
ClickUp web app and picks ClickUp's own autocomplete entry for each mention, so the
message carries real chips — and they survive copy-paste into another channel.

```python
from skills.clickup_browser import send
res = send(
    "8cr6661-7217",                      # chat channel id
    "Header\n\n---\n\n@everyone please check, [@Dana Lee](#user_mention#123)",
    team_id="9017956545",
    storage_state="local/sessions/clickup-palmetto.json",
)
res.everyone, res.users, res.plain       # True, ["123"], [] — plain = names with no pickable member
```

| Token | Becomes |
|---|---|
| `@everyone` | Everyone chip (channels only; ClickUp offers none in DMs) |
| `[@Name](#user_mention#<id>)` | that person's chip, picked by ClickUp user id (`item_<id>` option) |
| a `---` line | dropped (ClickUp turns it into a divider that moves the caret) |

Plain text is inserted, not typed: a typed `#`, `/` or `:` opens a ClickUp picker
that swallows the following keys. Mentioning someone outside the channel makes
ClickUp ask "Add to this channel?"; the sender always answers **Don't Add**.

**Session.** A Playwright `storage_state` of the operator's ClickUp sign-in
(Google SSO for adi@mypalmetto.co; `cu_refresh` lasts about a year). `send`
rewrites the file after each send so refreshed tokens persist. BHAGA keeps the
cloud copy at `gs://bhaga-scrape-cache/_session/clickup-<store>.json`
(`agents/bhaga/scripts/gcs_cache.py`); re-seeding is in `RUNBOOK.md`
§ Unavailability reminder. No secrets in config — the session file is the credential
(gitignored `local/`, GCS in the cloud).

Runs wherever `skills/_browser_runtime` runs (the `bhaga-daily-refresh` image has
Chrome; `bhaga-webhook` does not).

Tests: `python3 -m pytest skills/clickup_browser -q`.
