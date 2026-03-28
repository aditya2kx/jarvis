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
- Fixed Playwright MCP config (`--profile` -> `--user-data-dir`, explicit nvm PATH)
- Moved Playwright MCP to user-level `~/.cursor/mcp.json`
- Installed Chromium for Playwright MCP
- Added stronger session continuity rules so new chats resume from files, not chat history
- Verified direct Google Drive API access via local config/token refresh path
- Added local Drive parity tooling: reusable inventory command + shadow diff script
- Added direct Google Drive folder-creation helper for shadow-folder setup
- Cleaned up document-registry.json: normalized all drivePaths to numbered convention, deduplicated IDs (30-33 → 34-37), removed status suffixes from folder names, removed incorrect Expenses Partnership folder, updated emptyFolders
- Queried Google Sheet — confirms 31 documents tracked, all match registry
- Upgraded chitra-playbook.md: prior-year return is now the primary bootstrap input (not manual registry maintenance)
- Added "Handling User Design Feedback" protocol to jarvis.md
- Rewrote create_shadow_folders.py to be registry-driven (no hardcoded folder names) — works for any CHITRA user
- Refreshed benchmark inventory at `extracted/drive-2025-inventory.json`
- Improved derive_registry_from_return.py fuzzy matching (6.5% → 48.4% match rate)
  - Added issuer normalization (strip EINs, account numbers, legal suffixes)
  - Added docType aliasing (Consolidated 1099 → 1099, Form 1098 → 1098, etc.)
  - Generic issuer matching (Property Manager, County Tax Assessor → matches actual names)
- Created return-profile.schema.md — canonical JSON schema for tax return profiles
  - CHITRA uses this schema when parsing any user's tax return text
  - Covers all standard forms: 1040, Schedules A-E, 8889, 8949, 8582, K-1s
- Created generate_questionnaire.py — produces 35 friendly layperson questions
  - 19 Confirmation questions (did prior-year items change?)
  - 16 Discovery questions (life events the return can't predict)
  - Categories: Jobs, Investments, Rental, Partnerships, Business, Charitable, Health, Home, Life Events, Retirement, Education, Tax Payments
  - Each question explains WHY it's asked and WHAT to do if the answer is yes/no
- Created onboard_from_return.py — full new-user pipeline
  - Input: PDF (local or Drive ID) or existing profile JSON
  - Step 1: Extract text via pdfplumber
  - Step 2: Print parsing prompt + schema for CHITRA to produce profile JSON
  - Step 3: Derive registry + questionnaire from profile
  - Works for ANY user — no hardcoded names or entities
- Current match analysis: 15/31 registry docs derived from prior-year return alone (48.4%)
  - Remaining 16 are genuinely new-year events (new home, new CPA, DONUM note, employer payroll docs, retirement accounts, homestead exemption, etc.)
  - These are exactly the questions the questionnaire asks

### Prior Sessions (2026-03-27)
- Completed CHITRA v1: Phases A-D (git init, knowledge capture, browser automation, README)
- CPA email drafting and Homebase document handling
- Uploaded Palmetto employer tax docs (W-2, W-3, Form 941, Form 940) to Drive

## What's Next (v2 backlog)
1. ~~Add channels:join scope~~ DONE — bot invited to #all-jarvis manually
2. Install Playwright MCP and test with Fort Bend County (public, no login) — IN PROGRESS
   - Fixed: `--profile` → `--user-data-dir`, added env PATH for nvm, moved to user-level MCP config
   - Chromium browser binary installed
   - Remaining issue: Playwright MCP descriptors appear on disk, but runtime MCP tool list has not exposed `user-playwright` yet
3. Populate credentials/portals.yaml and Keychain entries for each portal
4. Test full OTP flow: Playwright login -> Slack OTP request -> continue
5. Fix validation gaps (docType normalization, Sheet tab names, estimates field names)
6. **Shadow folder validation (BLIND PARITY mode)**: Build `Taxes/2025-test` entirely from CHITRA's knowledge, automation, and user conversations — never look inside real `Taxes/2025`
   - Real folder is sealed; only opened for a final scored comparison
   - Derive folder structure from `drive-folder-convention.md` + `document-registry.json`
   - Derive filenames from naming conventions + document metadata
   - Ask user for any missing input data, configs, or credentials
   - Done: benchmark inventory captured (sealed), diff tooling ready, folder-creation helper ready
   - Next: create `2025-test` root folder, then derive and build subfolder structure from knowledge base
7. **New-user onboarding pipeline**: PDF → extracted text → CHITRA parsing → profile JSON → registry + questionnaire
   - Schema: `agents/chitra/knowledge-base/schema/return-profile.schema.md`
   - Questionnaire: `agents/chitra/scripts/generate_questionnaire.py`
   - Pipeline: `agents/chitra/scripts/onboard_from_return.py`
   - Tested: 35 questions generated, 22 docs derived, 8 folder categories

## North Star Vision
CHITRA's goal is fully autonomous tax document collection for ANY user:
1. User hands CHITRA their prior-year tax return PDF
2. CHITRA parses it into a structured profile (CHITRA-the-AI is the parser)
3. CHITRA derives 60-70% of the expected documents from the return
4. CHITRA asks ~35 friendly questions to fill the remaining 30-40% (life events, changes)
5. From answers, CHITRA autonomously figures out WHERE to get each document:
   - County property tax sites (derived from address → county lookup)
   - Broker portals (credentials in Keychain)
   - Employer HR portals
   - Insurance company sites
6. CHITRA navigates those sites (Playwright), downloads documents, uploads to Drive
7. User only provides: the PDF, answers to plain-English questions, and occasional permissions
8. End result: 100% populated Drive folder structure matching what a human would build

Current state: Steps 1-4 built and tested. Steps 5-6 now PROVEN — Playwright MCP works, Schwab login + tax form discovery succeeded, Fort Bend CAD property lookup autonomous. Steps 7-8 (download + upload) are built but need first real download test.

## Live Questionnaire Exercise Results (2026-03-28)
Simulated new-user onboarding using only `profile-2024.json` + user Q&A (no peeking at real registry).

**Starting point:** 22 docs derived from 2024 return alone (48.4% of real 31-doc registry)
**After 6 questions + answers:** 34 docs identified (~97% coverage of real registry)
  - 13 from return alone (no questions needed)
  - 10 from user answers (6 questions total)
  - 11 CHITRA would fetch autonomously via Playwright (zero user questions)

**Key learnings persisted to chitra-playbook.md:**
1. Check-yourself-first principle: never ask what you can check via portal/bank/public site
2. Question triage table: self-check vs bank-derived vs address-derived vs must-ask vs user-provides
3. Smart follow-ups: address → county → portal URL → homestead (auto-derive chain)
4. Employer HR portals = user provides (too much SSO friction)
5. Match user's tone, use names not "taxpayer/spouse"
6. Gmail is a document source — CPA correspondence + charitable docs (priority skill)
7. Bank transactions reveal insurance providers and property managers
8. Status reports > more questions ("Downloaded X, Y. Z isn't available yet — want me to email?")

**Portal registry created:** `credentials/portals.yaml.template` with 20+ portals mapped:
  - 8 Playwright-automatable (brokers, banks, county sites, insurance, payroll)
  - 4 Playwright+OTP (brokers with MFA)
  - 2 user-provides (employer HR with SSO)
  - 3 email-based (CPA, charitable, K-1 notifications)
  - Gmail skill identified as high priority (came up 2x in exercise)

## Immediate Next Steps (prioritized by impact)
1. **Complete E*Trade login** — user needs to provide SMS OTP (was on flight). Once done, download 1099 + Stock Plan Supplement
2. **Test actual PDF download** — we proved we can SEE the documents on Schwab; next: click download, save file, upload to Drive
3. **Enable Slack Socket Mode** — user needs to: generate App-Level Token (xapp-...), enable Socket Mode, add message.im event subscription
4. **Collect remaining portal credentials** — Robinhood, Wells Fargo, Chase, Fidelity, HSA, Homebase, Obie (conversational flow)
5. **Build Gmail skill** — high priority, came up twice in questionnaire exercise (DONUM docs, CPA correspondence)
6. **Wire answer-processing logic** — take questionnaire answers → auto-update derived registry → auto-derive portal list
7. **Build county tax bill scraper** — county tax assessor sites for actual tax payment receipts (CAD appraisal data already captured)
8. **Score against real registry** — run final diff of exercise-built registry vs actual document-registry.json

## Playwright E2E Tests (2026-03-28)
Successfully tested autonomous document discovery and login:
1. **County CAD** (public, no login) — searched by address
   - Found property record: appraised value, homestead exemption confirmed
   - Deed history, taxing jurisdictions, property details all extracted
   - Full autonomous discovery: address in → property data out, zero user interaction
2. **Charles Schwab** (authenticated, no MFA) — logged in with Keychain credentials
   - Navigated to Statements & Tax Forms
   - Found **1099 Composite and Year-End Summary - 2025 AVAILABLE** for both accounts
   - Account selector works: can switch between accounts
   - Clean logout verified
3. **E*Trade** (authenticated, MFA required) — logged in with Keychain credentials
   - Login successful, but MFA triggered (SMS to registered phone)
   - No email OTP option available (only SMS or alternate phone)
   - OTP request sent to user via Slack DM — deferred (user offline)
4. **Credential workflow validated**: store_credential.py → macOS Keychain → PortalSession.get_credentials() → Playwright fills login
5. **Slack OTP notification**: sent DM to user requesting OTP code, confirmed delivery

## Components Built This Session (2026-03-28)
1. **Slack Socket Mode listener** (`skills/slack/listener.py`)
   - Push model: WebSocket connection, Slack sends events instantly (no polling)
   - Writes OTP replies to `/tmp/jarvis-otp/{portal}.json` for instant pickup
   - `request_otp()` in adapter.py auto-detects Socket Mode vs polling fallback
   - Requires: App-Level Token (`xapp-...`) + Socket Mode enabled in Slack app
   - TODO: user needs to generate app-level token and enable Socket Mode + event subscriptions

2. **Portal automation framework** (`skills/browser/portal_session.py`)
   - `PortalSession` class: credential retrieval, OTP orchestration, download staging, Drive upload, registry update
   - Reusable for ANY portal: `session = PortalSession("Schwab")`
   - `get_credentials()`: reads from macOS Keychain
   - `request_otp()`: sends Slack DM, waits for reply (push or poll)
   - `stage_download()` + `upload_all()`: batch upload to Drive with auto-naming
   - `_update_registry()`: marks docs as received in document-registry.json
   - `list_keychain_portals()`: shows all stored portal credentials

3. **Portal navigation scripts** (`agents/chitra/scripts/portals/`)
   - `schwab.py`: exact Playwright MCP steps for login → tax forms → download
   - `fort_bend_county.py`: county property tax search steps (generic, works for any county)

4. **Slack adapter improvements**
   - `request_otp()` upgraded: phone_hint parameter, Socket Mode auto-detection
   - Config updated: `slack.primary_user_id` and `slack.dm_channel` stored
   - MFA-via-Slack rule added to chitra-playbook.md (CRITICAL: always notify via Slack, never rely on IDE)

5. **Credentials stored in Keychain**
   - jarvis-schwab, jarvis-etrade (usernames stored securely, never in git)

## Blockers
- ~~Playwright MCP is configured and Chromium is installed, but runtime MCP tool availability is inconsistent~~ **RESOLVED** — Playwright MCP is fully operational (tested 2026-03-28)
- ~~Slack Socket Mode not yet enabled~~ **RESOLVED** — App-Level Token generated, Socket Mode enabled, `message.im` event subscribed
- E*Trade requires SMS MFA — no email option, blocks fully autonomous login until Gmail skill or Slack Socket Mode is operational
- Some county .gov sites block automated browsers via Cloudflare — use CAD search sites (.org) instead
- Portal credentials partially populated — Schwab and E*Trade stored, ~10 more portals need creds
- Google Drive MCP read-only auth path is failing with a Google 403 — Drive work uses direct API helpers instead

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
- 2026-03-28: Shadow-folder validation is a BLIND test — never look inside real `Taxes/2025`, build everything from CHITRA's own knowledge + user input
- 2026-03-28: If mirror validation hits unresolved discrepancies, pause and ask the user instead of guessing
- 2026-03-28: Mirror-validation diffs should continuously drive Jarvis's next-step prioritization
- 2026-03-28: CHITRA's primary input for bootstrapping a tax year should be the prior-year federal/state returns — parse every schedule/form/issuer, derive the document checklist and folder structure from it, then pull docs autonomously using saved credentials. The registry is derived output, not manual input.
- 2026-03-28: Expenses Partnership folder was a misread of 2024 return — removed
- 2026-03-28: Auburn CA is a passive RE investment waiting on K-1 (reference 2024 return for context)
- 2026-03-28: No estimated tax payment docs for 2025; filing extensions in 2026
- 2026-03-28: Questionnaire exercise proved ~97% coverage achievable with 6 user questions + autonomous portal checks
- 2026-03-28: Check-yourself-first principle — CHITRA should attempt portal/site checks before asking the user
- 2026-03-28: Employer HR portals are user-provides — too much SSO friction to automate
- 2026-03-28: Gmail skill is high priority — CPA correspondence and charitable docs both live in email
- 2026-03-28: Portal credential registry uses Keychain for secrets, portals.yaml.template for portal metadata (URLs, auth methods, doc types)

- 2026-03-28: Slack Socket Mode (push) preferred over polling for OTP — instant delivery, no API quota waste
- 2026-03-28: MFA/OTP notifications MUST go via Slack DM, never rely on IDE messages (user may not be at computer)
- 2026-03-28: PortalSession class handles credential → login → OTP → download → upload → registry update lifecycle
- 2026-03-28: Portal navigation scripts are CHITRA-readable instructions, not standalone executables
- 2026-03-28: Schwab login works WITHOUT MFA; E*Trade always requires SMS MFA
- 2026-03-28: When portal offers email-based OTP, prefer it (future Gmail skill can read autonomously)

## Git State
- Branch: `main`
- Remote: configured (private SSH key)
- Public URL: https://github.com/aditya2kx/jarvis
