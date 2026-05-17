# BHAGA — Tip Allocation & Payroll Prep Agent

**Bhaga** (भग) — named after the Vedic Aditya whose name derives from the Sanskrit root *bhaj* ("to apportion, divide, share"). Bhaga is the deity of the **just distribution of wealth and shares** — the rightful portion due to each. The Sanskrit words *bhāga* (portion) and *bhāgya* (one's share, fortune) come from him.

*Each receives their rightful share.*

## What BHAGA Does

For the Austin shop (and future locations — Houston, etc.), BHAGA runs the end-of-day / end-of-pay-period tip workflow:

1. **Pulls daily tip totals from Square** — card tips per date via the Square Payments API
2. **Pulls daily clock-in/out hours per employee from ADP RUN** — Time > Timecards via Playwright (no API available for RUN small-business)
3. **Computes pool-by-day fair allocation** — each day's tip pool split proportionally to that day's hours, rolled up to per-employee per-period totals
4. **Writes the working ledger Google Sheet** — daily ledger, per-period summary, and a paste-ready block in ADP Time Sheet Import format
5. **Read-only toward ADP** — the human copies the paste block into RUN's Time Sheet Import once per pay period and approves (write-back to RUN is explicitly out of scope for v1)

## Execution Model

On-demand inside a Cursor/Jarvis session. No cron, no always-on box. Typical cadence:

- **Daily during the pay period** — refresh tips + hours, see the running ledger
- **Once at period close** — generate the paste block and hand off to RUN

## Design Principles

- **Each data source is its own skill** — Square extraction, RUN automation, allocation math, Sheet writing all live under `skills/` and are reusable by any agent
- **Allocation logic is pure (no IO)** — `tip_pool_allocation` is a pure function, easily unit-testable
- **Multi-store from day one** — every skill takes `location_id` / `shop_id` and a credential handle so Houston (September 2026) drops in without refactoring
- **Living sheet, not a one-time export** — re-running the agent updates the same sheet; it's idempotent per date
- **Browser automation is a stepping stone** — `adp_run_automation` uses Playwright today because RUN has no API; if/when ADP exposes one, the skill gets re-implemented behind the same interface (per `jarvis.md` § Conventions)

## Knowledge Base

Located at `agents/bhaga/knowledge-base/`:

| Path | Contents |
|------|----------|
| `schema/` | Data schemas: tip records, hours records, allocation output, ADP paste block format |
| `store-profiles/` | Per-store config: location IDs, ADP company code, earnings codes, pay period schedule, employee mapping (name ↔ ADP file #) |
| `selectors/` | Calibrated CSS/ARIA selectors for the ADP RUN Time > Timecards page, with `last_verified` dates |
| `learnings/` | Per-portal navigation patterns captured during live sessions |
| `*.json` | Active state — last run, cached session cookies pointer, per-period drafts (gitignored) |

## Skills Used

### Reused (existing)

- **`skills/browser/`** — Playwright portal automation. Powers `adp_run_automation`.
- **`skills/google_sheets/`** — Sheets create/read/populate. Powers `tip_ledger_writer`.
- **`skills/credentials/`** — macOS Keychain registry for the Square access token and ADP RUN login.
- **`skills/slack/`** — Optional end-of-run notifications and MFA prompts during ADP login.

### New (built for BHAGA, reusable by any future agent)

- **`skills/square_tips/`** — `GET /v2/payments` aggregator: daily tip totals by location.
- **`skills/adp_run_automation/`** — Playwright drive of ADP RUN Time > Timecards: per-employee daily hours.
- **`skills/tip_pool_allocation/`** — Pure-function pool-by-day fair share computation.
- **`skills/tip_ledger_writer/`** — Writes the daily ledger, period summary, and ADP paste-block tabs into the existing Austin tip ledger sheet.

## External Data Sources

| Source | MCP / Auth | What BHAGA pulls |
|--------|------------|-------------------|
| Square POS | Square Payments API + access token in Keychain | Card tip totals per date per location |
| ADP RUN Time Tracker | `user-playwright` + login in Keychain | Per-employee daily regular + OT hours |
| Austin tip ledger sheet | `user-palmetto-google` (or `user-google-drive-sheets` — confirm at first session) | Read existing layout, write daily/summary/paste-block tabs |
| User (Slack) | `user-slack` | MFA codes for first login per session, decisions on edge cases |

## Cursor Rules

BHAGA's behavior is defined at `.cursor/rules/bhaga.md` (auto-loads when working in `agents/bhaga/**`).

## Risk Acknowledgments (accepted by user)

- **ADP ToS**: Browser automation of own data with own credentials. Gray area. User-accepted risk.
- **UI fragility**: ADP redesigns the RUN Time Tracker UI periodically. Expect ~1 day of selector re-calibration per redesign. Recorded selectors live in `knowledge-base/selectors/` with `last_verified` dates.
- **Credential hygiene**: macOS Keychain only. Session cookies stored in Jarvis state, never the workspace repo. Credentials never in git.
- **MFA friction**: First run per session may require user interaction for the ADP MFA code. This is intentional — keeps a human in the loop for any access to RUN.

## Operational Constraints (must respect in the orchestrator)

These are subtle but consequential — captured here so M3's `daily_refresh.py` author doesn't have to rediscover them.

- **ADP excludes open shifts from the Timecard Excel export.** Any employee currently clocked in (no End Work punch) is silently omitted from that run's data. The daily refresh must fire AFTER all employees clock out for the day. Convention: run at `shop_close_local_time + 60 minutes` and scrape `T-1` (yesterday), never `T-0` (today). See `ORCHESTRATOR_SCRAPE_BUFFER_MINUTES_AFTER_SHOP_CLOSE` in `skills/adp_run_automation/shift_backend.py`.
- **Square timestamps are in the account's display timezone, not the shop's.** Palmetto's Square account is in Eastern Time but the Austin shop is Central Time. A transaction at 11:30 PM CT shows up as 12:30 AM ET on the next calendar day. `skills/square_tips/transactions_backend.parse_csv()` does the TZ conversion via `zoneinfo`; the orchestrator must pass `shop_tz` from the store profile.
- **ADP times ARE in shop-local TZ** (no conversion needed). The asymmetry between Square and ADP is intentional — don't try to "fix" ADP.
- **Some employees appear under multiple name spellings in ADP** because the manager edits names mid-period (e.g. adding a middle initial). The store profile's `employee_aliases` map normalizes these; the orchestrator must always pass it into `shift_backend.daily_shifts(employee_aliases=...)`. Failing to apply aliases will double-count an employee under both spellings in the model sheet.
- **ADP uses DIFFERENT name formats between reports.** The Timecard XLSX (shift hours) returns `"LastName FirstName"` (space-separated). The Earnings & Hours XLSX (wage rates) returns `"LastName, FirstName"` (comma-separated). Joining hours × rate in the model sheet requires a single canonical form — the store profile's `employee_aliases` map MUST contain entries for both spellings of every employee, mapping to one canonical form. Sample for Palmetto: `{"Alvarez Sebastian": "Alvarez, Sebastian", "Alvarez, Sebastian": "Alvarez, Sebastian"}`. Recommend keeping the comma form as canonical (it's what ADP uses everywhere except the time-tracking module). The M2 model-sheet author must verify a clean join (every shift hour has a matching rate) and alert via slack on any orphans.
- **No salaried employees in this ADP RUN account today.** All 12 employees including the store manager (Lindsay Krause @ $25/hr) are hourly. `is_salaried=False` for the entire roster. Exclusion of the manager from labor% and the tip pool is by NAME via `store_profile.excluded_from_tip_pool_and_labor_pct`, NOT via the salaried flag. The `is_salaried` inference logic in `compensation_backend.infer_wage_rates()` is preserved for future-proofing (other stores may have salaried managers).
- **"Earnings and Hours V1" is a per-store saved custom report.** It exists in Palmetto's ADP RUN account and was set up by the user. New stores onboarding to BHAGA must create the equivalent saved report (with the same 8 columns: Employee Name, Payroll Check Date, Period Start/End Date, Earning Hours, Hourly Earning Rate, Earning Amount, Earning Description) before `compensation_backend` works for them. Store profiles capture the report name as `adp_wage_rate_report_name` (defaults to `"Earnings and Hours V1"`).

## Out of Scope for v1

- Automated write-back to ADP Time Sheet Import (human pastes)
- Cron / scheduled runs
- Multi-location orchestration in a single invocation (Austin only in v1; Houston drops in via different `location_id` + credential handle)
- Per-day tip payout (tips ride on paycheck — confirmed)
- Square Team setup / per-employee Square attribution
- Replacing ADP RUN with a different time tracker

## Agent Naming Convention

Jarvis agents are named after figures from Sanskrit/Hindu mythology and Indian history whose role matches the agent's purpose:

| Agent | Named After | Role |
|-------|------------|------|
| CHITRA | Chitragupta — divine scribe, keeper of all records | Tax document collection and organization |
| CHANAKYA | Chanakya — economist, strategist, author of Arthashastra | Product research, market analysis, business strategy |
| AKSHAYA | Akshaya Patra — the inexhaustible divine vessel of food | Inventory forecasting, demand prediction, supply chain ordering |
| BHAGA | Bhaga — Vedic Aditya, the apportioner of shares | Tip pool fair division, hours-based allocation, payroll prep |

## Reference

- Handoff doc: `get open/handoff-tip-allocator-agent.md`
- Origin chat: [Square ADP tip automation plan](b8a58719-e992-4051-954d-dbd513cf0f93)
- Sibling agent (similar pattern — Square + Playwright + Sheets): AKSHAYA
