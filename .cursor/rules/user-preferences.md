---
description: User preferences and accumulated context (auto-loaded)
alwaysApply: true
---

# User Preferences

Predictive model of Aditya's communication style, design principles, domain context, and prior decisions. Auto-loaded by Cursor at the start of every chat.

**Single source of truth (Fork 5):** This file does NOT restate Hard Lessons (`.cursor/rules/jarvis.md` § Hard Lessons). When a preference is already a Hard Lesson, the `Source` column links to it. When a preference is NEW signal not yet codified elsewhere, it lives here.

Maintained by `skills/user_model/`. Source corpus (gitignored) at `skills/user_model/data/corpus.jsonl`.

## Communication style

| # | Pattern | Source |
|---|---|---|
| 1 | Terse summaries with batched decisions, not serialized one-question-at-a-time | observed across 2026-04-18/19 chat |
| 2 | Surface trade-offs explicitly when there are multiple valid approaches; never silently pick | .cursor/rules/dev-workflow-decisions.mdc + this session |
| 3 | Tables work well for status reports and fork comparisons | observed |
| 4 | Put decisions near the top of the response, not buried at the bottom | .cursor/rules/dev-workflow-decisions.mdc |
| 5 | Cross-reference rather than restate when info already exists elsewhere | this session 2026-04-19 (Fork 5 pick) |
| 6 | Use `markdown citations to existing files` when discussing them, not full path repeated each time | observed |
| 7 | Be honest about what's working vs broken; precise terminology on failure modes (e.g. 'browser context closed' not 'MCP broken') | this session 2026-04-19 (Hard Lesson #11 update) |

## Design principles

| # | Principle | Source |
|---|---|---|
| 1 | If a third-party service has a self-serve developer dashboard, build a `skills/<service>_app_provisioning/` skill — never ask the user to do manual web steps | jarvis.md HL#0 |
| 2 | When 'separate' could mean 'looks separate' (cosmetic) vs 'is separate' (real identity), always ask before silently picking the cheaper option | jarvis.md HL#1 + this session |
| 3 | Single source of truth — never duplicate content across files; cross-reference instead | this session 2026-04-19 (Fork 5) |
| 4 | Prefer parallel conversations over serialized when multiple threads are blocked on different inputs | this session 2026-04-19 |
| 5 | Browser automation is a stepping stone, not the destination — every Playwright-based extraction has a backlog item to migrate to the service's REST API | jarvis.md § Conventions |
| 6 | Permission/scope choices: never silently pick the lesser scope and discover the failure later | .cursor/rules/dev-workflow-decisions.mdc |
| 7 | Execute explicit user instructions before improvising alternatives — don't substitute your idea of better for what the user asked | .cursor/rules/dev-workflow-decisions.mdc § Execution Order |
| 8 | When user gives N instructions in one message, address all N — don't hyper-focus on one and forget the rest | .cursor/rules/dev-workflow-decisions.mdc |
| 9 | Hard Lessons / corrections must persist to a file in the same turn. 'Noted' without a file write means the mistake will repeat | jarvis.md Hard Lesson #4 |
| 10 | macOS Keychain only for secrets; never plaintext config, never in git | jarvis.md § Conventions |
| 11 | Prefer building Jarvis skills with direct API calls (urllib.request + OAuth) over depending on third-party packages with low maintenance signal | jarvis.md HL#16 |
| 12 | Slack is the async communication channel for any user-input-needed moment; Cursor IDE is co-equal but not assumed | jarvis.md HL#7 |
| 13 | Workspace restart is the canonical Playwright recovery; do NOT silently fall back to `cursor-ide-browser` MCP | jarvis.md HL#11 |
| 14 | Build a predictive user model proactively — capture preference signals as they appear, not only as corrections after they fail | this session 2026-04-19 (this skill exists) |
| 15 | Passkey-gated portals (Touch ID / FIDO2 / WebAuthn) can NOT be automated via stored password — pivot to persistent Playwright browser-profile/ for session-cookie reuse. User authenticates with biometric once, session persists for days/weeks, Touch ID re-prompts only on session expiry. Do NOT capture 'something password-shaped' as a fallback — it's likely the Mac login or another service's password, not the portal's. | 2026-04-20 ADP RUN discovery (user logs in via Touch ID, no ADP password exists) |
| 16 | The collaborative-browser credential interceptor should detect passkey-only flows and abort capture rather than storing whatever text-shaped input appears. Heuristic: if the portal never shows a password field during normal auth (only biometric prompts), suppress capture. Until that's implemented, ALWAYS test captured creds by running a from-scratch login before persisting — don't trust the capture blindly. | 2026-04-20 ADP — captured a 12-char password that was not the ADP password; deleted from Keychain on discovery |

## Domain context

| Topic | Detail | Source |
|---|---|---|
| User | Aditya Parikh (with Kajri Shah) | config.yaml profile.taxpayer_names |
| Filing status | MFJ (2025 tax year, US) | config.yaml profile.filing_status |
| Business | Palmetto Superfoods (smoothie + bowl franchise) — holding co | PROGRESS.md, raw-intake notes |
| Stores | Austin (opened 2026-03-23, soft opening); Houston launching September 2026 | PROGRESS.md AKSHAYA section |
| POS | Square — recipes/prices controlled by HQ | AKSHAYA & BHAGA agent docs |
| Payroll | ADP RUN (small-business bundle, not Workforce Now). Time Tracker accessed via Playwright (no API for RUN) | BHAGA agent docs + handoff-tip-allocator-agent.md |
| Inventory | MarketMan ($396/mo, known pain point) — being augmented by AKSHAYA | PROGRESS.md AKSHAYA |
| Slack workspace | jarvis-coa3805 (T0ANZQAK85V) | config.yaml + provisioning runs |
| Google accounts | Palmetto (adi@mypalmetto.co) + personal | config.yaml accounts section |
| Property | Primary residence: 1414 Crown Forest Dr, Missouri City TX (Fort Bend); rental: Brisbane CA | CHITRA tax data |
| OS | macOS (Apple Silicon) | observed |
| Tools | Cursor IDE with Playwright + multi Google MCPs + Slack MCP + ClickUp MCP + Chronosphere MCP | config.yaml mcps directory |

## Decision history

| Date | Decision | Picked | Why |
|---|---|---|---|
| 2026-04-18 | Tip-allocator agent name | BHAGA over Aryaman / Yudhishthira | Etymology: bhaj = 'to apportion / divide' — direct semantic match for fair-share tip division. Tone matches existing CHITRA/AKSHAYA functional names rather than epic-hero names. |
| 2026-04-18 | BHAGA Slack identity (transitional) | Use CHITRA bot with [BHAGA] prefix temporarily | User stepping away; needed reachability immediately. Real Slack app deferred until Playwright-driven provisioning skill existed. |
| 2026-04-19 | BHAGA Slack identity (real) | Created separate BHAGA Slack app via Playwright + skills/slack_app_provisioning/ | User pushed back on cosmetic prefix — wants actual BHAGA bot user in sidebar. Triggered building the canonical `<service>_app_provisioning/` pattern. |
| 2026-04-19 | Square authentication model | PAT (Personal Access Token, full account scope) over OAuth (PAYMENTS_READ scoped) | Single-user self-use shop. Trust model already accepted for every other Keychain-stored portal credential. OAuth migration deferred to v2 if BHAGA ever runs on a server / is shared. |
| 2026-04-19 | user_model design forks | 1A / 2A / 3A / 4A + cross-reference HLs (Fork 5) | Heuristic capture (highest signal-to-noise) + inline confirmation (prevents wrong rules) + single auto-loaded file (matches Hard Lessons pattern that's already working) + skill not new agent + single source of truth (no content duplication). |
| 2026-04-19 | Manifest Slack app_home defaults | Add `messages_tab_enabled: true` + `messages_tab_read_only_enabled: false` to default + BHAGA manifest | Slack defaults Messages tab to read-only since 2022 — users see DMs from bot but can't reply. Caught by user testing reply to BHAGA after first provisioning. |
| 2026-04-19 | Multi-agent Slack listener architecture | One Socket Mode listener process per agent with identity_mode=real, all reading into agent-tagged inbox files | Socket Mode is app-scoped — BHAGA's app token can't be served by CHITRA's listener. Rejected single-listener-multi-app because that's a more invasive refactor for marginal gain. |
| 2026-04-20 | ADP RUN authentication model | Playwright browser-profile session persistence (cookie-based) | ADP account is passkey-only (Touch ID) with no password to store. Browser-profile already persists cookies across runs. User does Touch ID once; session survives days/weeks. When expired, user re-auths with one biometric tap. No credentials stored in Keychain for ADP — different model than Square (which had a real password). |
