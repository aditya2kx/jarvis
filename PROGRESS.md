# Jarvis Build Progress

## Current Phase
Jarvis architecture restructure complete. Slack skill operational.

## Last Session (2026-03-28)
- Restructured repo from flat CHITRA layout to Jarvis agent/skill hierarchy
- Renamed workspace: Tax Strategies -> Jarvis
- Renamed GitHub repo: chitragupta -> jarvis
- Moved 29 git-tracked files to new locations (core/, skills/, agents/chitra/)
- Moved gitignored files (knowledge-base JSON, scripts/personal/, 2025/ data)
- Updated imports in all 8 scripts (sys.path bootstrap + core.config_loader)
- Created Jarvis coordinator rule (.cursor/rules/jarvis.md)
- Created Slack skill (skills/slack/adapter.py) with send_message, read_replies, request_otp
- Stored Slack bot token in macOS Keychain (service: jarvis)
- Added Slack MCP to user-level ~/.cursor/mcp.json
- Tested Slack connection: DM sent successfully to workspace owner
- Updated all configs (config.template.yaml, config.yaml, .gitignore, .cursor/mcp.json)

### Prior Sessions (2026-03-27)
- Completed CHITRA v1: Phases A-D (git init, knowledge capture, browser automation, README)
- CPA email drafting and Homebase document handling
- Uploaded Palmetto employer tax docs (W-2, W-3, Form 941, Form 940) to Drive

## What's Next (v2 backlog)
1. Add `channels:join` scope to Slack app (or invite bot to channels manually)
2. Install Playwright MCP and test with Fort Bend County (public, no login)
3. Populate credentials/portals.yaml and Keychain entries for each portal
4. Test full OTP flow: Playwright login -> Slack OTP request -> continue
5. Fix validation gaps (docType normalization, Sheet tab names, estimates field names)
6. Merge jarvis-legacy/agents/email (email-intelligence-pipeline) into agents/email

## Blockers
- Slack bot not in channels (DMs work; need channels:join scope or manual invite)
- Playwright MCP not yet installed
- jarvis-legacy folder needs cleanup (contains old email agent project)

## Completed Steps
- [x] CHITRA v1 — Phases A-D (commits 7cea51d → fa1e88c)
- [x] Jarvis architecture restructure
  - Workspace renamed: Tax Strategies -> Jarvis
  - GitHub repo renamed: chitragupta -> jarvis
  - Directory hierarchy: core/, skills/, agents/chitra/
  - 29 files moved via git mv
  - All imports updated
  - jarvis.md coordinator rule created
  - Slack skill created and tested
  - Config templates updated with slack section

## Decisions Log
- 2026-03-27: Public repo (open-source the framework)
- 2026-03-27: Passwords via macOS Keychain, never plaintext
- 2026-03-27: Playwright MCP for browser automation, Slack MCP for OTP
- 2026-03-27: No PII in any git commit
- 2026-03-28: Restructure to Jarvis coordinator + agent/skill hierarchy
- 2026-03-28: jarvis/ folder conflict resolved by renaming old to jarvis-legacy/
- 2026-03-28: Slack MCP in user-level ~/.cursor/mcp.json (not workspace — secrets)
- 2026-03-28: Portal playbooks under agents/chitra/ (domain knowledge, not generic skill)
- 2026-03-28: sys.path bootstrapping for imports (pyproject.toml deferred to v3)

## Git State
- Branch: `main`
- Remote: `git@github.com-personal:aditya2kx/jarvis.git`
- Public URL: https://github.com/aditya2kx/jarvis
- Local config: user.email=aditya.2ky@gmail.com, user.name=adi2ky
