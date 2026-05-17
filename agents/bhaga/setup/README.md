# BHAGA Slack App Setup

> **Preferred path: use `skills/slack_app_provisioning/` to automate this end-to-end.**
> The procedure below is a manual fallback for use only if Playwright + the
> automated skill cannot be run (e.g. Slack admin UI redesign that hasn't been
> re-calibrated yet, or running in an environment without Playwright). For
> normal use, run:
>
> ```bash
> python -m skills.slack_app_provisioning.provision --agent bhaga
> # …drive Playwright through the returned plan, capture xoxb- + xapp- tokens, then…
> python -m skills.slack_app_provisioning.register \
>     --agent bhaga --bot-token xoxb-... --app-token xapp-...
> ```
>
> The skill does steps 1–4 below + Keychain storage + config wiring +
> first-DM-as-real-bot in one transaction. See `skills/slack_app_provisioning/README.md`.
>
> ---
> **Manual fallback procedure (only if the skill is unavailable):**

This guide creates a real, separate Slack app for BHAGA so it appears as its own bot user in your sidebar (not as CHITRA-with-a-prefix). Sibling reference: the CHANAKYA Slack app, set up the same way.

**Time required**: ~5 minutes once you're at api.slack.com.

## Step 1 — Create the app from manifest

1. Open <https://api.slack.com/apps?new_app=1> (works on phone too)
2. Choose **From a manifest**
3. Pick workspace: **jarvis-coa3805** (your existing Jarvis workspace)
4. Paste the entire contents of [`slack-app-manifest.yaml`](slack-app-manifest.yaml) into the YAML/JSON box
5. Click **Next** → review → **Create**

The manifest pre-configures: bot user named "BHAGA", purple background, all needed scopes (`chat:write`, `im:*`, `users:read`, etc.), event subscriptions (`message.im`, `app_mention`), and Socket Mode enabled.

## Step 2 — Install to workspace

1. On the new app's settings page, click **Install to jarvis-coa3805**
2. Approve the requested permissions
3. Slack will redirect you to **OAuth & Permissions** with a `xoxb-...` **Bot User OAuth Token** at the top

**Send me that token via Slack DM** (`xoxb-...`). I'll store it immediately in Keychain and delete the message from the DM history.

## Step 3 — Generate the App-Level Token (for Socket Mode)

1. On the app settings page, click **Basic Information** in the left sidebar
2. Scroll to **App-Level Tokens** → **Generate Token and Scopes**
3. Token name: `socket-mode`
4. Add scope: **`connections:write`**
5. Click **Generate** → copy the `xapp-...` token

**Send me that token too via Slack DM** (`xapp-...`). Same drill — Keychain + delete the message.

## Step 4 — Invite BHAGA to DM you

1. In Slack, click the **+** next to "Direct messages" in the sidebar
2. Search **BHAGA** and start a DM
3. Send any message (e.g., "hi") so the DM channel is created and BHAGA can reply

That's it. As soon as I have the two tokens and the new DM channel ID, I:
- Store both tokens in Keychain (`SLACK_BOT_TOKEN_BHAGA` service `jarvis-bhaga`, `SLACK_APP_TOKEN_BHAGA` service `jarvis-bhaga`)
- Update `config.yaml` `slack.agents.bhaga` with the real `bot_token_cmd` + new DM channel + `identity_mode: "real"`
- Restart the listener so it picks up BHAGA's app-level token (or run a separate listener instance per agent — TBD; for now we share the existing CHITRA listener since it covers `message.im` for any DM the bot is invited to in the workspace)
- Send the first BHAGA-as-real-bot DM as confirmation

After the flip, the `[BHAGA]` text prefix stops being applied — the bot's display name itself is BHAGA, so the prefix becomes redundant.

## What's in the manifest (and why)

| Field | Why |
|-------|-----|
| `bot:chat:write` | Send DMs to user |
| `bot:chat:write.customize` | Future option: per-message username override (NOT used by default — real identity comes from the bot user itself) |
| `bot:im:history` + `im:read` + `im:write` | Read user replies in the DM, open DM channels |
| `bot:users:read` + `users:read.email` | Resolve your user ID, find users by email if needed |
| `bot:app_mentions:read` + `channels:history`/`groups`/`mpim` | Future: channel-mention support if you ever invite BHAGA into `#all-jarvis` |
| `socket_mode_enabled: true` | Push delivery for messages — no polling, instant pickup |
| `bot_events: message.im, app_mention` | The two event types the listener subscribes to |
| `token_rotation_enabled: false` | Long-lived tokens; matches CHITRA / CHANAKYA setup. Can enable later. |

## If anything goes sideways

- **"App not approved for workspace"** — your workspace requires admin approval for new apps. As workspace owner you can self-approve in the workspace's app management page.
- **Token doesn't start with `xoxb-` / `xapp-`** — you copied the wrong field. `xoxb-` is on **OAuth & Permissions**; `xapp-` is on **Basic Information** > App-Level Tokens.
- **Socket Mode not connecting later** — the `xapp-` token must have the `connections:write` scope. Regenerate if missing.
