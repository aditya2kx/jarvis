# skills/slack_app_provisioning

Provisions a real, separate Slack app for any Jarvis agent — fully via the existing browser + credentials skills. No manual web-UI homework for the user; if the user's `api.slack.com` session needs login or MFA the collaborative-browser pattern hands the keyboard back to them just for that step, then resumes.

**Reusable across every Jarvis agent.** The agent-creation checklist in `.cursor/rules/jarvis.md` calls this skill as the canonical "give the new agent its own Slack identity" step.

## Why it exists

Per `jarvis.md` Hard Lesson "Don't ask the user to do web UI steps when existing browser+credentials skills can automate them": creating a Slack app is just web navigation against `api.slack.com` with the user's existing logged-in session. CHITRA already drives Schwab, Wells Fargo, E*Trade, Just Appraised, etc. via the same `skills/browser/` + `skills/credentials/` pattern. There is no reason Slack admin should be different.

## What it provisions (one call → finished agent identity)

1. Generates an agent-specific manifest from the default template (overridable per-agent at `agents/<name>/setup/slack-app-manifest.yaml`)
2. Drives Playwright (the `user-playwright` MCP) through `api.slack.com/apps?new_app=1`:
   - Click "From a manifest" → select target workspace → paste YAML → Next → Create
   - Install to workspace → approve scopes
   - Capture the `xoxb-...` bot token from OAuth & Permissions
   - Generate App-Level Token (`xapp-...`) with `connections:write` scope on Basic Information
3. Stores both tokens in Keychain via `skills/credentials/registry.add_keychain()`:
   - Service `jarvis-<agent>`, accounts `SLACK_BOT_TOKEN_<AGENT>` and `SLACK_APP_TOKEN_<AGENT>`
4. Resolves the new agent's DM channel ID by opening a DM with the user using the new bot token
5. Updates `config.yaml` `slack.agents.<agent>` with the real `bot_token_cmd`, `dm_channel`, and flips `identity_mode → "real"` (which automatically stops applying the `[AGENT] ` text prefix)
6. Sends a confirmation DM as the new bot user

If any step fails, the skill writes what it captured so far so a partial run can resume — same idempotency model as `ensure_listening.py`.

## Public API

```python
from skills.slack_app_provisioning import provision, register

# Step A: produce a plan + manifest (no Playwright yet)
plan = provision.build_plan(
    agent_name="bhaga",
    workspace_slug="jarvis-coa3805",
    manifest_path="agents/bhaga/setup/slack-app-manifest.yaml",  # optional; auto-generated if absent
)
# plan is a list of structured steps the AI agent executes against the user-playwright MCP
# (browser_navigate / browser_snapshot / browser_click / browser_type / browser_evaluate)

# Step B: after Playwright captures tokens, finalize
register.register_agent_identity(
    agent_name="bhaga",
    bot_token="xoxb-...",
    app_token="xapp-...",
    user_id="U0APJRE5DC4",   # for opening the DM channel
)
# This stores in Keychain, updates config.yaml, opens DM, sends confirmation, returns dm_channel_id.
```

## Files

| File | Purpose |
|------|---------|
| `provision.py` | Manifest generator + Playwright playbook builder |
| `register.py` | Post-Playwright finalizer (Keychain + config + DM open + confirmation) |
| `default_manifest.yaml` | Template manifest used when an agent has no per-agent override |

## Agent-specific overrides

If an agent wants a non-default manifest (custom display name, color, scopes), it ships one at `agents/<name>/setup/slack-app-manifest.yaml`. The skill loads that path first, falls back to `default_manifest.yaml` and substitutes the agent's name into the template.

## Failure modes & recovery

| Failure | What the skill does |
|---------|---------------------|
| Slack session not logged in | Collaborative-browser handoff: Slack DM via existing CHITRA bot → "Please log in to api.slack.com in the Playwright browser, reply 'done' when ready" |
| Workspace selector shows multiple options | Snapshot + ask user to disambiguate via Slack |
| Manifest validation error | Surface the exact Slack error text via Slack DM, do not retry |
| Token capture failed (UI changed) | Snapshot the page, send to user via Slack, ask which selector to use, persist the new selector to `agents/<name>/knowledge-base/selectors/slack_admin.json` for future runs |
| Keychain write failed | Abort, report which command failed |
| `config.yaml` write failed | Abort, leave Keychain entries intact (idempotent re-run safe) |

## Multi-agent reuse (the whole point)

Future `agents/narada/`, `agents/vidura/`, etc. — exactly one command:

```bash
python -m skills.slack_app_provisioning provision --agent narada
```

…and Narada has its own bot user in your Slack sidebar. No web-UI homework, no manual Keychain commands, no manual config edits.
