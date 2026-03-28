# Slack Skill

Enables Jarvis agents to communicate via Slack — send messages, read replies, and handle OTP/2FA flows during automated portal logins.

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

### 2. Install and get token

1. Click **Install to Workspace** and authorize
2. Copy the **Bot User OAuth Token** (`xoxb-...`)

### 3. Store token securely

```bash
security add-generic-password -a SLACK_BOT_TOKEN -s jarvis -w "xoxb-YOUR-TOKEN-HERE"
```

### 4. Add to config.yaml

```yaml
slack:
  workspace: your-workspace-name
  bot_token_cmd: "security find-generic-password -a SLACK_BOT_TOKEN -s jarvis -w"
  notification_channel: "#general"
```

### 5. Add Slack MCP to user-level config (optional)

If using the Slack MCP server in Cursor, add to `~/.cursor/mcp.json` (NOT the workspace config):

```json
{
  "mcpServers": {
    "slack": {
      "url": "https://mcp.slack.com/mcp",
      "headers": {
        "Authorization": "Bearer xoxb-YOUR-TOKEN-HERE"
      }
    }
  }
}
```

## Usage

```python
from skills.slack.adapter import send_message, request_otp, test_connection

# Verify connection
info = test_connection()

# Send a message
send_message("#general", "Jarvis is online.")

# Request OTP during portal login
otp = request_otp(user_id="U12345", portal_name="Schwab", timeout_seconds=300)
```

## OTP Flow

1. Agent encounters 2FA during portal login
2. Calls `request_otp()` with the portal name
3. Bot DMs the user asking for the verification code
4. User replies with the code
5. Agent receives the code and continues login
