# CHITRA Build Progress

## Current Phase
COMPLETE — All phases shipped. Browser automation tests deferred (see Deferred Items).

## Last Session (2026-03-27)
- Completed Phase A: git init with PII-clean initial commit (7cea51d)
- Completed Phase B: schemas, playbook, examples, rule refactoring (e61d5c3)
- Completed Phase C: MCP config, 12 portal playbooks, collector (055a1da)
- Completed Phase D: README, PII review, pushed to GitHub (fa1e88c)
- Pushed to: https://github.com/aditya2kx/chitragupta

## What's Next
1. Test browser automation when Playwright MCP is installed (portal-test-public, portal-test-otp)
2. Set up Slack MCP with bot token for OTP notifications
3. Populate credentials/portals.yaml and Keychain entries for each portal
4. Fix validation gaps (docType normalization, Sheet tab names, estimates field names) — v2 cleanup

## Blockers
- **Browser tests**: Playwright MCP not yet installed; Slack MCP needs bot token; credentials/portals.yaml not yet populated

## Completed Steps
- [x] Phase A: Git repo setup (commit 7cea51d)
  - config.template.yaml, config_loader.py, .gitignore
  - Refactored 13 scripts to use config_loader
  - PII scripts moved to scripts/personal/ (gitignored)
  - chitra.md + chitra-workflows.md made profile-agnostic
  - populate_sheet.py nav_data externalized to registry-driven generation
- [x] Phase B: Knowledge capture (commit e61d5c3)
  - 4 schema docs (document-registry, tax-profile, drive-folder, estimates)
  - chitra-playbook.md (8 sections, 418 lines)
  - 5 anonymized example JSON files
  - Validation gate completed (structural alignment confirmed)
- [x] Phase C: Browser automation (commit 055a1da)
  - .cursor/mcp.json (Playwright MCP + Slack MCP)
  - portals.template.yaml (Keychain-based credential template)
  - 12 portal playbook YAMLs
  - portal_collector.py orchestrator
- [x] Phase D: README + PII review + Push (commit fa1e88c)
  - README.md with architecture, setup, browser automation, v2 roadmap
  - Deep PII scan: zero personal data across all 4 commits
  - Pushed to git@github.com-personal:aditya2kx/chitragupta.git

## Deferred Items
- **portal-test-public**: Test browser automation with Fort Bend County (no login). Deferred because Playwright MCP is not yet installed/running.
- **portal-test-otp**: Test browser automation with Schwab (OTP via Slack). Deferred because: (1) Playwright MCP not installed, (2) Slack MCP needs SLACK_BOT_TOKEN, (3) credentials/portals.yaml not populated, (4) Keychain entries not created.
- **Validation gap fixes**: docType enum normalization, Sheet tab name alignment, estimates field naming — documented in validation results below; not blocking for v1.

## Validation Gate Results
| Area | Result | Notes |
|------|--------|-------|
| Drive folder tree | PASS | 01-10 categories match |
| Document registry | PARTIAL | docType enum needs canonical normalization |
| Sheet tabs | PARTIAL | Playbook uses generic names; script uses year-prefixed |
| Estimates | PARTIAL | Schema names vs actual field names differ |
| CPA Navigator | PASS | 4 columns align |
| Email draft | PARTIAL | Section structure diverges from template |

## Decisions Log
- 2026-03-27: Public repo (open-source the framework)
- 2026-03-27: Passwords via macOS Keychain, never plaintext
- 2026-03-27: Playwright MCP for browser automation, Slack MCP for OTP
- 2026-03-27: Local-first (laptop must be running); cloud agents as v2 roadmap
- 2026-03-27: No PII in any git commit — clean before committing, not after
- 2026-03-27: PII scripts moved to scripts/personal/ instead of committing then cleaning
- 2026-03-27: Validation gate = structural match, not exact; naming gaps documented for v2 cleanup
- 2026-03-27: Browser automation tests deferred — infrastructure not yet ready; framework code committed
- 2026-03-27: Git push uses `github.com-personal` SSH host alias (personal key, not work key)
- 2026-03-27: portals.template.yaml moved to repo root (credentials/ dir is gitignored)
- 2026-03-27: Repo name: chitragupta (not chitra). GitHub user: aditya2kx (not adi2ky)

## Git State
- Branch: `main`
- Remote: `git@github.com-personal:aditya2kx/chitragupta.git`
- Public URL: https://github.com/aditya2kx/chitragupta
- Commits: 4 (7cea51d → e61d5c3 → 055a1da → fa1e88c)
- Files tracked: 38
- Total size: ~147KB
- Local config: user.email=aditya.2ky@gmail.com, user.name=adi2ky
