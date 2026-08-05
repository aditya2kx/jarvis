"""Thin ClickUp Chat (v3) REST client.

ClickUp's MCP servers (both user-clickup and @twofeetup/clickup-mcp) are
task-only and don't expose chat-message reads. This skill plugs that gap
by calling ClickUp's v3 chat endpoints directly with a PAT.

Public API:
    from skills.clickup_chat import (
        get_pat, list_channels, find_channel_by_name, fetch_messages,
        post_message, ensure_dm_channel,
    )

PAT lookup: macOS Keychain service `jarvis-clickup-palmetto-pat`
            (registered as `clickup_palmetto_pat` in skills/credentials).
"""

from .runner import (
    ensure_dm_channel,
    fetch_messages,
    find_channel_by_name,
    get_pat,
    list_channels,
    post_message,
)

__all__ = [
    "ensure_dm_channel",
    "fetch_messages",
    "find_channel_by_name",
    "get_pat",
    "list_channels",
    "post_message",
]
