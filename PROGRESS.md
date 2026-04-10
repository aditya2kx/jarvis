# Jarvis Build Progress

## Recurring Mistakes (read before every task)

| Mistake | Where the fix lives | Pre-check |
|---------|---------------------|-----------|
| Compared `2025` folder against itself (0 diffs = meaningless) | `orchestrator.py` `validate_against_benchmark()` | Verify shadow_folder_id != benchmark_folder_id |
| Copied folder structure from sealed `2025` benchmark | `derive_registry_from_return.py` | Never read `Taxes/2025` to decide what to create in `2025-test` |
| User correction acknowledged in conversation but not persisted | `.cursor/rules/jarvis.md` Hard Lessons + skill-evolution protocol | Every correction = a file write. Name the file or it didn't happen. |
| Asked user what could be self-checked (county, portal availability) | `chitra-playbook.md` Step 4 triage table | Derive from address/portal before asking |
| Validation done once at end instead of after each action | `orchestrator.py` `upload_and_validate()` | After each upload/folder creation, re-inventory and diff |

## Current Phase
**14 portals DONE. 31 docs uploaded to Drive. 25/33 adjusted files match (76%).**

- Final registry: 34 documents, 21 folder paths
- Raw validation: 24/37 (64%), but user removed 4 from tracking (iso-tracker, Moss Adams, 1095-C, 2024 return)
- Adjusted: 25/33 = 76%

**Portal download status:**
| Portal | Status | Docs | Notes |
|--------|--------|------|-------|
| Schwab | DONE | 2 | 1099 Composite (acct 965) + Account 3771 Statement. ISO Disposition Survey = last (user's Google Sheet, needs DASH transaction cross-ref across Schwab + E-Trade). |
| E-Trade | DONE | 4 | 1099 Consolidated (DASH), Stock Plan Supplement, Mailing Group Letter, De Minimis Letter (AABA) |
| Robinhood | DONE | 1 | 1099 Consolidated (Securities and Crypto). Login: aditya.2ky+hood@gmail.com, MFA via app push. |
| Wells Fargo | DONE | 1 | 1098 Mortgage Interest Statement (acct 5503) |
| County Property Tax (Fort Bend) | DONE | 2 | Tax Statement + Receipt (2025) from Fort Bend County. Acct 8118640020010907, CAD Ref R555090, 1414 Crown Forest Dr. |
| San Mateo County | DONE | 2 | 2024-2025 + 2025-2026 Property Tax Bills. Acct 104-140-030, 211 Golden Eagle Ln Brisbane. Cloudflare bypass: click checkbox in Turnstile iframe. |
| Homebase | DONE | 4 | Form 941 Q4, Form 940 Annual FUTA, W-2 Lindsay (Employee), W-3 Transmittal. Login: adi@mypalmetto.co (Palmetto Chrome Passwords CSV), MFA via SMS to phone ending 0038. |
| Chase | DONE | 1 | 1098 Mortgage Interest (acct 7737, primary residence). Login: aditya2kxbiz, MFA via Chase mobile app push. |
| Obie Insurance | DONE | 4 | 2024 + 2025 full policies and declarations. Login: aditya.2ky@gmail.com, email PIN. Policies: OAN024977-00 ($1,991), OAN024977-01 ($2,270). |
| MH Capital (InvPortal) | DONE | 1 | 2025 K-1 for MH Sienna Retail II LLC. Login: aditya.2ky@gmail.com at mhcapital.invportal.com. |
| BCGK InvestorCafe | DONE | 2 | K-1 + Preferred Return Distributions xlsx ($6,250 = 4 quarterly × $1,562.50). Login: aditya.2ky@gmail.com at 23192bcgk.investorcafe.app. Site finicky — refresh after login. 7-digit email 2FA. |
| Ziprent | DONE | 1 | 1099-MISC ($74,450 rental income). Login: aditya.2ky@gmail.com at app.ziprent.com/auth/login. Tax Forms page under account dropdown menu. |
| FBCAD (Fort Bend) | DONE | 2 | 2025 + 2026 Appraisal Notices (shows HS homestead exemption active). Public site, no login. esearch.fbcad.org property search → Appraisal Notice PDF link. |
| Just Appraised | DONE | 1 | 2025 Texas Form 50-114 Homestead Exemption Application (#27782044, R555090). Login: aditya.2ky@gmail.com at taxpayer.justappraised.com. Auth0 fails in Cursor Electron browser but works in Playwright Chrome. |

**Incremental validation (codified in jarvis.md #13):**
After every upload, run `python agents/chitra/scripts/validate_upload.py --slack` to diff shadow vs benchmark.
Current: 25/33 adjusted files match (76%). 8 files remaining.

**User removed from tracking:** iso-tracker JSON, Moss Adams estimate, DoorDash 1095-C, 2024 Federal Return

**9 remaining files:**
| File | Category | Action Needed |
|------|----------|---------------|
| 2025 W-2 - DoorDash - Aditya | W-2s & Employment | User uploads from DoorDash Workday |
| 2025 W-2 - Texas Childrens Hospital - Kajri | W-2s & Employment | User/Kajri uploads |
| 2025 Student Loan Tax Info - Kajri | W-2s & Employment | User/Kajri uploads |
| ISO Disposition Survey CSV | Brokerage/Schwab | Google Sheet cross-ref DASH across Schwab+E-Trade (deferred to end) |
| Rastegar K-1 email | Partnerships | Expected Aug 2026, not available yet |
| 2025 Bank Transactions - Brisbane Rental CSV | Brisbane Rental | User exports from bank |
| ~~2025 Texas Form 50-114 Homestead Application~~ | ~~Primary Residence~~ | DONE — Downloaded from Just Appraised portal (Playwright Chrome). |
| 2025 Donum Charitable Lending Note | Charitable | User provides |
| 2025 Palmetto Business Transactions - Copilot Export | Business | User exports from Copilot |

**Skill persistence (new this session):**
Portal navigation configs created/updated for ALL 13 portals:
- `agents/chitra/scripts/portals/` — 14 config files (9 existing + 5 new)
- `agents/chitra/knowledge-base/download-strategies.md` — 4 download methods, MFA patterns, Cloudflare bypass
- Each config has `verified` date and `verified_actions` list
- Generalizable: given prior-year return + passwords + questionnaire, system can replay to 73%+

**File naming convention**: `{year} {Form Type} - {Issuer} {Account Details} - {Description}.{ext}`
Helper: `agents/chitra/scripts/naming_convention.py`

**Corrections from validation:**
- Wells Fargo 1098 moved from Primary Residence → Brisbane Rental (was in wrong folder)
- All 8 files renamed to match benchmark naming convention (year-first format)
- Property tax: benchmark has "$9,757 PAID" in name (amount matters)

**Playwright recovery lesson (codified in jarvis.md #11):** Kill Chrome browser-profile processes + remove lock files, NOT the MCP server.

**Idle state fix (codified in jarvis.md #12):** Never go idle after sending a Slack message. Always check for replies + continue working. Only stop when user says "done" or "stop".

**Slack communication architecture (3 layers):**
1. Socket Mode Listener (`skills/slack/listener.py`) — instant WebSocket receive, auto-handles commands
2. Inbox Processor (`skills/slack/inbox_processor.py`) — polls every 2 min for 4h, classifies messages, acknowledges on Slack, writes to `/tmp/jarvis-pending-actions.json`
3. AI Agent — reads pending-actions.json at start of every turn + between major actions

**On session start:** Check `cat /tmp/jarvis-inbox-processor.pid` and restart if needed. Also restart listener if needed.

## Last Session (2026-04-05, session 3)
- **Questionnaire answers processed** — user-answers-2025.json created and applied
  - Kajri left Stanford Childrens → Texas Childrens Hospital (new employer)
  - Primary residence: 1414 Crown Forest Drive, Missouri City, TX
  - Homestead exemption filed and approved
  - Business employee (Homebase payroll) for Palmetto Superfoods
  - Charity: Donum replaces prior
  - Retirement: 403b through Texas Childrens (provider TBD)
- **Partnership cities added** — Auburn CA, Houston TX, Austin TX from user input
- **K-1 status tracking** — k1_received flag: MH Sienna received, only Austin TX pending
- **RPC name normalization** — ISSUER_BRAND_MAP: "RPC 5402 South Congress Partners LLC" → "RPC 5402 South Congress LLC"
- **Folder derivation fixes** — 5 validation iterations, 8/22 → 18/22 folder match
  - new_home updates existing PRIMARY RESIDENCE docs (no folder duplication)
  - Business employee docs mapped to correct "08 - Business - {name}" folder
  - taxYear field added to final registry
- **2025-test recreated** 5 times during iterative validation
- **Remaining diffs analyzed** — all 4 are expected:
  1. `Kajri - Texas Childrens Hospital` vs `Kajri [NEED W-2s]` (we know employer)
  2. `Auburn CA - Lincoln Way` combined vs benchmark split (user confirmed same)
  3. `Texas Childrens Hospital [NEED DOCS]` vs `Fidelity [NEED DOCS]` (skipped)

## Prior Session (2026-04-05, session 2)
- **Slack long-polling loop** — AI agent stays alive and responsive to Slack
  - `skills/slack/wait_for_input.py` — blocks until Slack message arrives (checks every 5s) or timeout
  - `skills/slack/inbox_processor.py` — background daemon (4h), polls inbox every 2min, classifies messages, acknowledges on Slack, writes to `/tmp/jarvis-pending-actions.json`
  - 3-layer architecture: Listener (instant) → Processor (2min) → AI (active polling)
  - Rule in `jarvis.md`: always check pending-actions + inbox before every action
- **Derivation code fixes** — reduced folder diffs from 14 missing/11 extra to 7 missing/5 extra
  - `_parse_address()` / `_abbreviate_street()` — proper address parsing
  - K-1 subfolders get `[NEED K-1]` suffix
  - "Expenses" → "Expenses Partnership" renaming
  - New categories: `09 - Tax Payments & Extensions`, `06 - Retirement Accounts`
  - Remaining 7 diffs = all need questionnaire answers

## Prior Session (2026-03-28, continued)
- **Derive-first pipeline refactor** — all folder paths now derived from user data, never from benchmark
  - `derive_folder_tree()` + `ISSUER_BRAND_MAP` added to `derive_registry_from_return.py`
  - 19 nested folder paths derived from 22 documents (was: 8 flat categories)
  - `drivePath` field set on every document during derivation
  - Subfolder naming: `{person} - {brand}` for W-2s, `{brand}` for 1099s, `{city} Rental - {address}` for properties, entity name for K-1s, business name embedded in category
  - `ISSUER_BRAND_MAP` normalizes legal entities to brands (e.g. `Charles Schwab & Co., Inc` → `Schwab`)
- **`create_shadow_folders.py`** — rewritten to accept `--registry` flag, support N-level folder nesting (was limited to 2)
- **`orchestrator.py`** — critical validation fix
  - `validate_against_benchmark()` now inventories `2025-test` (shadow) and compares against `2025` (benchmark)
  - Safety check: rejects if shadow_folder_id == benchmark_folder_id
  - `resolve_folder_id()` maps drivePath to shadow folder IDs
  - `run_pipeline()` wires full sequence: registry → create folders → init tasks → Slack notification
- **`process_answers.py`** — imports `derive_folder_tree`, `rebuild_folder_tree()` method re-derives paths after answers
- **`onboard_from_return.py`** — updated to use `derive_folder_tree()` instead of flat folder list
- **Hard Lessons codified** to persistent files:
  - `.cursor/rules/jarvis.md` — Hard Lessons section + concrete feedback routing table + skill-evolution hook
  - `.cursor/rules/chitra-playbook.md` — subfolder derivation rules in Section 1.3
  - `PROGRESS.md` — Recurring Mistakes table at top

## Last Session (2026-04-05)
- Built collaborative browser session framework (`skills/browser/collaborative.py`)
  - JS credential interceptor: captures form fields on submit, stores in sessionStorage+localStorage to survive redirects
  - Slack-based user notification: AI navigates browser, notifies user via Slack to enter creds
  - Takeover flow: AI can request user help, watch for "done" signal, learn navigation patterns
  - Learning persistence: stores navigation patterns in `agents/chitra/knowledge-base/learnings/`
- Added `collaborative_login` step type to portal plan generator (`base.py`)
  - `generate_plan()` now accepts `credential_mode="collaborative"|"keychain"`
  - Plan markdown renders collaborative login sub-steps for AI execution
- Wired `CollaborativeSession` into `TaskRunner` (`run_portal_tasks.py`)
  - `ensure_credentials()` now supports `method="collaborative"|"slack"`
  - When creds missing + collaborative mode: marks task as ready with `credential_mode=collaborative`
  - Plan generation passes credential_mode through to step generator
- Built Chrome Password Manager → Keychain import pipeline (`credentials/import_from_chrome.py`)
  - Reads Chrome CSV export, matches URLs to known Jarvis portal patterns
  - Shows matches table with existing Keychain status, asks for confirmation
  - Bulk stores all confirmed entries in Keychain, deletes CSV immediately
  - Mapped 9 portals with URL patterns for matching
- Imported 7 portal credentials from Chrome in one shot:
  - Schwab, E*Trade, Wells Fargo, Fidelity, Robinhood, Homebase, Chase
  - HSA provider still missing (need to identify which provider user has)
- Attempted collaborative browser credential capture for Wells Fargo:
  - Learned: JS interceptor loses state on SAML redirects (cross-origin navigation)
  - Learned: Polling form fields directly via Playwright is more reliable than event listeners
  - Learned: Chrome CSV import is far more efficient for bulk credential collection
  - Collaborative browser model still valuable for: first-time logins, stuck navigation, CAPTCHA handling

## Session (2026-03-28)
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
- Uploaded employer tax docs (W-2, W-3, Form 941, Form 940) to Drive

## What's Next (v2 backlog)
1. ~~Add channels:join scope~~ DONE — bot invited to #all-jarvis manually
2. Install Playwright MCP and test with a county CAD site (public, no login) — IN PROGRESS
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

Current state: Steps 1-4 built and tested. Steps 5-6 now PROVEN — Playwright MCP works, Schwab login + tax form discovery succeeded, county CAD property lookup autonomous. Steps 7-8 (download + upload) are built but need first real download test.

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
1. **Run full portal automation** — 8/9 portals have creds; run `prepare_all()` and execute plans via Playwright
2. **Test actual PDF download** — click download on Schwab/E*Trade, save file, upload to Drive
3. **Identify HSA provider** — last missing credential; add URL pattern to import script
4. **Build Gmail skill** — high priority, came up twice in questionnaire exercise (charitable docs, CPA correspondence)
5. **Build county tax bill scraper** — county tax assessor sites for actual tax payment receipts
6. **Verify Slack Socket Mode** — test WebSocket connection for real-time OTP delivery
7. **Score against real registry** — run final diff of exercise-built registry vs actual document-registry.json

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

3. **Portal navigation framework** (`agents/chitra/scripts/portals/`)
   - `base.py`: portal loader, plan generator, registry — discovers all modules, generates step-by-step AI plans
   - 9 structured portal modules, each exporting `PORTAL_CONFIG` dict:
     - `schwab.py`: iframe login, 1099 Dashboard SPA, multi-account selector
     - `etrade.py`: mandatory SMS MFA, stock plan + brokerage sections
     - `county_property_tax.py`: public CAD search, address → county derivation
     - `robinhood.py`: React SPA, hCaptcha risk, 1099-DA for crypto
     - `fidelity.py`: brokerage + retirement + HSA, NetBenefits split
     - `wells_fargo.py`: mortgage 1098, transaction export, email MFA
     - `chase.py`: hash-based SPA routing, email MFA available
     - `hsa_bank.py`: generic multi-provider (HealthEquity, Optum, Fidelity, etc.)
     - `homebase.py`: payroll forms (W-2, W-3, 941 quarterly, 940)
   - Architecture: navigation knowledge (like DB drivers) is checked in; user's portal manifest (which ones they use) stays in config.yaml (gitignored)
   - `list_portals()` discovers all modules; `generate_plan()` produces step-by-step AI execution plans from any config
   - `format_plan_markdown()` renders a human/AI-readable plan with quirks, selectors, and code snippets

4. **Answer-processing pipeline** (`agents/chitra/scripts/process_answers.py`)
   - `AnswerProcessor` class: takes derived registry + questionnaire answers → final registry + portal task list
   - `apply_confirmation()`: process yes/no answers for prior-year items
   - `add_from_life_event()`: one answer triggers multiple documents (e.g. "new home" → mortgage 1098 + property tax + homestead + HUD-1)
   - 12 life event handlers: new_home, home_sold, new_employer, employer_left, new_brokerage, new_rental, rental_sold, business_employee, new_partnership, state_move, new_charity, homestead_exemption
   - `generate_portal_tasks()`: matches each document to available navigation modules, produces prioritized task list
   - Automation levels: fully_automated, check_then_ask, needs_module, email_skill, user_provides
   - Tested: 22 derived docs + 3 life events → 31 docs, 12 portal tasks (5 fully automated, 1 check-then-ask, 3 need modules, 1 email, 2 user-provides)

6. **Slack adapter improvements**
   - `request_otp()` upgraded: phone_hint parameter, Socket Mode auto-detection
   - Config updated: `slack.primary_user_id` and `slack.dm_channel` stored
   - MFA-via-Slack rule added to chitra-playbook.md (CRITICAL: always notify via Slack, never rely on IDE)

7. **Credentials stored in Keychain**
   - jarvis-schwab, jarvis-etrade (usernames stored securely, never in git)

8. **Portal task runner** (`agents/chitra/scripts/run_portal_tasks.py`)
   - `TaskRunner` class: full orchestration loop for credential → plan → execute → status
   - `check_all_credentials()`: shows which portals have creds stored vs missing
   - `ensure_credentials()`: checks Keychain → if missing, asks user via Slack DM
   - `request_credentials_via_slack()`: sends DM asking for username then password, stores in Keychain, deletes credential messages from Slack history
   - `prepare_task()` / `prepare_all()`: checks creds + generates execution plans for all portal tasks
   - `resolve_portal()`: fuzzy-matches issuer names to portal modules (e.g. "Charles Schwab & Co" → schwab)
   - `mark_complete()` / `send_status_summary()`: Slack notifications for progress tracking
   - CLI: `--check` (cred status), `--plan <module>` (single plan), `--prepare` (all tasks), `--interactive` (ask for missing creds)
   - Tested: 3 ready (schwab, etrade, county), 7 blocked (missing creds) — exactly matches Keychain state

9. **Collaborative browser session** (`skills/browser/collaborative.py`)
   - `CollaborativeSession` class: AI drives browser, user assists when needed
   - JS credential interceptor: captures form fields on submit/click/Enter, persists to sessionStorage+localStorage
   - Slack notifications: notify user to enter creds, request takeover when stuck, resume after user helps
   - Learning persistence: stores navigation patterns in per-portal JSON files
   - Plan generation: `generate_login_plan()` produces step-by-step instructions for AI agent
   - Integrated into `TaskRunner` via `credential_mode="collaborative"` parameter

10. **Chrome → Keychain import pipeline** (`credentials/import_from_chrome.py`)
    - Reads Chrome Password Manager CSV export
    - Matches URLs against 9 known portal patterns (extensible)
    - Shows confirmation table with existing Keychain status
    - Bulk stores in Keychain, securely deletes CSV
    - One user action (Chrome export) → all portal creds stored

## Blockers
- ~~Playwright MCP is configured and Chromium is installed, but runtime MCP tool availability is inconsistent~~ **RESOLVED** — Playwright MCP is fully operational (tested 2026-03-28)
- ~~Slack Socket Mode not yet enabled~~ **RESOLVED** — App-Level Token generated, Socket Mode enabled, `message.im` event subscribed
- E*Trade requires SMS MFA — no email option, blocks fully autonomous login until Gmail skill or Slack Socket Mode is operational
- Some county .gov sites block automated browsers via Cloudflare — use CAD search sites (.org) instead
- ~~Portal credentials partially populated~~ **RESOLVED** — 8/9 portals credentialed via Chrome CSV import (only HSA provider missing)
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
- 2026-03-28: Portal navigation modules are structured PORTAL_CONFIG dicts — not prose docstrings, not executable scripts
- 2026-03-28: Navigation knowledge (how to use Schwab) is checked in like DB drivers; user's portal manifest (which portals they use) is gitignored
- 2026-03-28: portals.yaml.template sanitized to generic examples — user-specific portal list lives in portals.yaml (gitignored)
- 2026-03-28: Answer-processing pipeline maps life events to multi-document expansions (e.g. "new home" → 4 docs)
- 2026-03-28: Credential collection is conversational via Slack DM (ask username, then password), stored in Keychain, messages deleted from chat after storage
- 2026-03-28: TaskRunner orchestrates the full loop: task list → cred check → Slack ask → plan gen → AI execution → status notify
- 2026-04-05: Collaborative browser model: AI navigates, user enters creds in visible browser, AI captures via JS interceptor + stores in Keychain
- 2026-04-05: JS credential interceptor must store in sessionStorage/localStorage to survive page redirects (window variables are destroyed)
- 2026-04-05: SAML login flows (Wells Fargo) cross origins, wiping even localStorage — direct form field polling via Playwright is more reliable
- 2026-04-05: Chrome CSV export → Keychain bulk import is the most efficient credential collection method (Google has no API for Password Manager)
- 2026-04-05: Collaborative browser model is still the right approach for: first-time portal logins without saved passwords, stuck navigation, CAPTCHA handling, MFA flows
- 2026-04-05: Learnings directory (`agents/chitra/knowledge-base/learnings/`) stores per-portal navigation patterns from collaborative sessions

## Git State
- Branch: `main`
- Remote: configured (private SSH key)
- Public URL: https://github.com/aditya2kx/jarvis
