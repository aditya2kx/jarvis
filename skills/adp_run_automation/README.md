# skills/adp_run_automation

Playwright-driven extraction from **ADP RUN** for BHAGA's labor model.

## Implemented scrapes (`runner.py` — drive these via `download_adp_bundle`)

All three run in **one browser session / one login / one OTP** via
`download_adp_bundle(...)`; standalone entry points exist for ad-hoc runs.

| Function | Source | Output | Parser |
|---|---|---|---|
| `download_timecard` | Reports → Time → Timecard | `Timecard-<date>.xlsx` (per-punch) | `shift_backend.py` |
| `download_earnings` | Reports → saved "Earnings and Hours V1" | `Earnings-…-<date>.xlsx` (wage rates + CC-tips). From/To is **check date**, not period dates. | `compensation_backend.py` |
| `download_schedule` | Home → **Team Schedule** ("Manage Schedules") | `Schedule-<date>.json` (per-day scheduled hours, current + next week) | `schedule_backend.py` |
| pay_info rate refresh (in bundle) | People → Payroll info → Hourly pay rate | `PayInfoRates-<date>.json` | `pay_info_backend.py` |
| `payroll_draft_backend.run_draft` | Payroll Home → Start (flagged) | In-progress worksheet **left for operator** | Headless by default; stores Preview hours + Gross in `payroll_draft_runs` (no Preview URL); never Approve/Save; Monday 07:00 + `/payroll` **Run ADP Preview** |

**Wage-rate dual source (Issue #213 / #251):** Earnings Regular after payroll.
Nightly Payroll info scrapes **all recent punchers** so raises land before the
next check (OT / salaried flags from earnings are preserved). Per-employee
failures do not fail Timecard/tips.
CLI: `python3 -m skills.adp_run_automation.pay_info_backend --from-bq-punchers --write-bq`.

**Effective-dated history (Issue #343).** Both loaders also append to
`adp_wage_rate_history` (`wage_rate_history.py`) when a rate differs from the latest
history row, so a raise prices only shifts on/after its effective date. pay_info
records ADP's "Added on" date (`added_on`) and dates the change by
`resolve_effective_date`; earnings dates it at the paid period's start. Operator
override / inspection:
`python3 -m skills.adp_run_automation.wage_rate_history set --employee "Last, First" --effective YYYY-MM-DD --rate 18.00 [--dry-run]`
and `... wage_rate_history show --employee "Last, First"`.

**Directory match is alias-aware (Issue #343).** The Directory may list a
middle initial the canonical name lacks (`Johnson, Dolce J` for `Johnson, Dolce`).
`select_directory_match(..., accepted_names=...)` accepts a single row whose name
is an `employee_aliases` spelling of the same person; ≥2 candidates, or an exact
plus an alias row, still raise `AmbiguousEmployeeError` (never guess a rate).

**Timecard Notes.** `shift_backend.parse_xlsx` keeps the optional "Notes" cell as
`note` (→ `adp_punches.note`); materialize uses it for admin-punch tip exemptions.

**Alert on outcome, not mechanism (2026-09).** `report_pay_info_issues` Slacks only
when `remaining_gaps` (punchers with no rate from *any* source) is non-empty, or when
a flow error stopped the check running. A scrape failure whose employee already has a
rate via `rate_source = earnings` is a breadcrumb, not an alert — the point of having
two sources is that either may fail harmlessly. The previous behaviour DMed a "Failed
scrapes" alert every night from August for two employees who had valid rates
throughout, and an alert that is wrong nightly is one you stop reading.

**Directory gotchas (2026-09-13 live spike).**

- **The Status filter defaults to Active only.** Terminated employees are invisible to
  a Directory search no matter how long it waits — this is why `Flores, Juan` failed
  every night rather than intermittently. `clear_directory_status_filter()` ticks
  Terminated + Leave of absence before searching (roster 14 → 20). Filtering by
  employment status is wrong by construction here: a terminated employee still has
  hours in their final period, and hours are what require a rate.
- **ADP fires a full-viewport "Session Timeout" modal on idle.**
  `div.message-box-outer`, `position: fixed`, `z-index: 20000`. It intercepts pointer
  events, producing exactly `TimeoutError: Locator.click: Timeout 10000ms exceeded`,
  and Escape does not close it. `dismiss_blocking_modals()` clicks **Ok** — never
  Cancel, which signs the session out. Because an unanswered modal poisons every
  subsequent employee in the loop, it is re-checked at each click
  (`_click_through_modals`) and after each failure, not only once up front.
- **There is no ADP employee ID available yet.** `employee_id` in `adp_punches` is
  identical to `canonical_name`; the Directory DOM exposes only
  `aria-label="Go to the profile page for <Name>"`. With the status filter cleared,
  `Johnson, Dolce` (Terminated) and `Johnson, Dolce J` (Active) both appear, so
  `select_directory_match()` requires an **exact** name match and raises
  `AmbiguousEmployeeError` rather than guessing — a missing rate is recoverable from
  earnings, a wrong rate is silently wrong pay. Capturing ADP's associate ID from the
  profile page is a follow-up.
- **Failure evidence goes to GCS.** `_capture_pay_info_failure` routes through
  `_browser_runtime._capture_failure_evidence` (`gs://<cache>/<date>/evidence/`). It
  previously wrote to `~/.bhaga/state/screenshots`, a path that does not survive a
  Cloud Run execution — which is why no evidence exists for any failure since
  2026-08-24 despite the code appearing to capture it.

**Team Schedule scrape** (added 2026-06-10): ADP exposes NO structured export
for the schedule (Actions → "Print schedule" only opens the browser's native
print preview), so we scrape the grid DOM. The grid renders in
`iframe[name="timePartnerFrame"]`; per-day footer totals are light-DOM
`<team-schedule-total>` elements (`"N Employees\n HH:MM Hrs"`). The week
selector + ‹ › chevrons live in **Shadow DOM** (use Playwright text/role
locators, which pierce open shadow roots; raw `querySelectorAll` misses them).
The scrape is forward-looking (current + next week), best-effort (a failure is
non-fatal to the nightly run), and lands in BQ `adp_scheduled_daily` via
`backfill_from_downloads.py` → Grafana panel "Scheduled Hours vs Goal Hours".
Selector/flow knowledge is codified in `schedule_backend.py` (constants +
`SCHEDULE_EXTRACT_JS`); parser is unit-tested in `test_schedule_backend.py`.

> The sections below are the original M2 scaffold notes; kept for the timecard
> calibration history. The implemented behavior is the table above.

**Status (original):** scaffold only. To be implemented in BHAGA milestone M2.

## Why this exists (and why it's not an API call)

ADP RUN is the small-business bundle. Daily clock-in/out punch data is **not** exposed via:

- RUN Custom Reports (confirmed — Custom Reports does not include timecard punch data)
- Reports > Payroll > Payroll Summary (only pay-period totals, not daily)
- RUN's native API (gated behind ADP Marketplace partner agreement; economically unviable for a single shop)

The data IS visible in the web UI under Time > Timecards (per-employee daily breakdown with a Print button). Browser automation of that page is the only path to daily hours while staying on RUN.

ADP API Central exists only for **Workforce Now** (mid-market product), not RUN. Confirmed in the source chat: [Square ADP tip automation plan](b8a58719-e992-4051-954d-dbd513cf0f93).

## Built on top of

- `skills/browser/` — Playwright session management (already wired with the `user-playwright` MCP)
- `skills/credentials/` — macOS Keychain registry for the ADP login (username/password) and the cached session cookie pointer
- `skills/slack/` — `request_otp(...)` for MFA prompts on first login per session

## Public API (planned)

```python
from skills.adp_run_automation import pull_daily_hours

records = pull_daily_hours(
    start_date="2026-04-01",
    end_date="2026-04-14",
    credential_handle="adp_run_austin",   # registered in skills.credentials.registry
)
# -> [{"employee_id": "12345", "employee_name": "Maria Garcia", "date": "2026-04-01", "reg_hours": 7.5, "ot_hours": 0.0}, ...]
```

## Flow (planned)

1. Resolve credentials from Keychain via `skills.credentials.registry.lookup(credential_handle)`
2. Open `https://runpayroll.adp.com/enrollment.aspx` in a Playwright session (the bare `runpayroll.adp.com` was retired 2026-06-28 and now redirects to `sorry.adp.com`)
3. Reuse cached session cookie if valid; otherwise log in
4. If MFA required: pause, send Slack DM via `skills.slack.adapter.request_otp("ADP RUN", phone_hint=...)`, wait for reply, enter code
5. Persist session cookie for reuse across same-session subsequent runs (minimize MFA re-challenges)
6. Navigate left sidebar → **Time** → **Timecards**
7. Iterate (employee × date range), parse the daily breakdown table (date, regular hours, OT hours)
8. Return structured records

## Calibration knowledge

DOM selectors for the Time > Timecards page are NOT stable across ADP UI revisions. The skill reads selectors from `agents/bhaga/knowledge-base/selectors/run_timecards.json` (with `last_verified` date). If selectors fail:

1. Capture page snapshot via `browser_snapshot`
2. Send to user via Slack with the failed selector
3. Ask user to walk through the page; capture new selectors
4. Update `run_timecards.json` with new selectors + new `last_verified` date

First calibration must happen during a live session with the user logged in to ADP — the skill cannot self-calibrate.

## Risk acknowledgments (per `agents/bhaga/README.md`)

- Browser automation of own data with own credentials is gray-area in ADP's ToS. User-accepted.
- UI fragility: budget ~1 day of selector re-calibration per ADP redesign.
- Credential hygiene: Keychain only. Session cookies in Jarvis state, not in repo.

## Multi-store

`credential_handle` is a parameter. Each shop registers its own ADP RUN login under a distinct handle (`adp_run_austin`, `adp_run_houston`, ...). The skill itself contains no shop-specific logic.

## Future migration

If ADP ever opens RUN to direct API access at non-Marketplace pricing, this skill is re-implemented behind the same `pull_daily_hours(...)` interface — no caller changes required. Per `jarvis.md` § Conventions: "browser automation is a stepping stone, not the destination."
