# Gmail Skill

Native Gmail API integration for Jarvis. Uses direct `urllib` calls — no external dependencies.

## Modules

| Module | Functions | Description |
|--------|-----------|-------------|
| `auth.py` | `get_gmail_token(account)` | OAuth flow + token refresh for Gmail scopes |
| `search.py` | `search_messages(query)`, `list_messages(label)` | Search / list messages |
| `read.py` | `read_message(id)`, `get_message_body(payload)` | Read full message content |
| `attachments.py` | `list_attachments(id)`, `download_attachment(...)` | List & download attachments |
| `send.py` | `send_message(...)`, `reply_to_message(...)` | Send new emails or reply in-thread |
| `labels.py` | `list_labels()`, `create_label(name)`, `apply_labels(...)`, `remove_labels(...)` | Label management |

## Setup

Gmail uses a separate OAuth flow from the MCP server (which only grants Drive/Sheets scopes).

**First-time setup:**
```bash
cd Jarvis
python3 -m skills.gmail.auth --account palmetto
```

This opens a browser for consent, saves refresh token to `~/.../google-mcp-auth/palmetto/.gmail-credentials.json`.

**Prerequisites:**
- GCP project must have Gmail API enabled
- OAuth client must have `http://localhost:8089` as an authorized redirect URI (for Desktop app type, this is automatic)
- `config.yaml` must have the account configured under `accounts:`

## Usage (from Python)

```python
from skills.gmail.search import search_messages
from skills.gmail.read import read_message

results = search_messages("from:vendor@example.com subject:invoice", account="palmetto")
for r in results:
    msg = read_message(r["id"], account="palmetto")
    print(msg["body"][:500])
```

## CLI

Each module is runnable:
```bash
python3 -m skills.gmail.search "subject:inventory report" --account palmetto
python3 -m skills.gmail.read MESSAGE_ID --account palmetto
python3 -m skills.gmail.labels list --account palmetto
```
