# Slack Skill

Enables Jarvis agents to communicate via Slack — send messages, read replies, and handle OTP/2FA flows during automated portal logins.

## Architecture

Two modes for receiving user replies:

| Mode | How it works | Latency | Setup |
|------|-------------|---------|-------|
| **Socket Mode** (preferred) | WebSocket push — Slack sends events instantly | ~1 second | App-Level Token + enable Socket Mode |
| **Polling** (fallback) | Calls `conversations.history` in a loop | 10-30 seconds | Nothing extra |

`request_otp()` automatically uses Socket Mode if configured, otherwise falls back to polling.

## Setup

### 1. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** > **From scratch**
3. Name it (e.g. `Jarvis`) and select your workspace
4. Under **OAuth & Permissions**, add these **Bot Token Scopes**:
   - `chat:write` — send messages
   - `channels:read` — list channels
   - `im:write` — send DMs
   - `im:read` — read DMs
   - `im:history` — read DM history (for OTP replies)
   - `users:read` — look up users

### 2. Install and get bot token

1. Click **Install to Workspace** and authorize
2. Copy the **Bot User OAuth Token** (`xoxb-...`)

### 3. Enable Socket Mode (recommended)

1. Go to **Settings** > **Socket Mode** > Toggle ON
2. Go to **Basic Information** > **App-Level Tokens** > Generate Token
   - Name: `jarvis-socket`
   - Scope: `connections:write`
3. Copy the token (`xapp-...`)
4. Go to **Event Subscriptions** > Toggle ON
5. Under **Subscribe to bot events**, add:
   - `message.im` — DM messages to the bot

### 4. Enable Messages Tab

1. Go to **App Home** > **Show Tabs**
2. Check **Messages Tab**
3. Check **Allow users to send Slash commands and messages from the messages tab**

### 5. Store tokens securely

```bash
# Bot token (required)
security add-generic-password -a SLACK_BOT_TOKEN -s jarvis -w "xoxb-YOUR-BOT-TOKEN"

# App-level token (for Socket Mode)
security add-generic-password -a SLACK_APP_TOKEN -s jarvis -w "xapp-YOUR-APP-TOKEN"
```

### 6. Add to config.yaml

```yaml
slack:
  workspace: your-workspace-name
  bot_token_cmd: "security find-generic-password -a SLACK_BOT_TOKEN -s jarvis -w"
  notification_channel: "#general"
  primary_user_id: "UXXXXXXXXXX"
  dm_channel: "DXXXXXXXXXX"
```

## Usage

### Send messages

```python
from skills.slack.adapter import send_message, test_connection

info = test_connection()
send_message("#general", "Jarvis is online.")
```

### Request OTP during portal login

```python
from skills.slack.adapter import request_otp

otp = request_otp(
    user_id="U12345",
    portal_name="E*Trade",
    phone_hint="+1-XXX-XXX-XXXX",
    timeout_seconds=300,
)
```

### Start Socket Mode listener (background)

```python
from skills.slack.listener import start_listener_background

started = start_listener_background()  # returns False if no app token
```

### Start listener as standalone daemon

```bash
python skills/slack/listener.py &
```

## OTP Flow (with Socket Mode)

1. Agent encounters 2FA during portal login
2. Calls `request_otp()` — sends Slack DM to user
3. Socket Mode listener receives the user's reply instantly via WebSocket
4. Listener writes OTP to `/tmp/jarvis-otp/{portal}.json`
5. `request_otp()` picks up the file and returns the code
6. Agent enters the code and continues login

## OTP Flow (polling fallback)

1-2 same as above
3. `request_otp()` polls `conversations.history` every 10 seconds
4. When a reply appears, returns the code
