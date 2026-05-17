# skills/adp_run_automation

Playwright-driven extraction of per-employee daily clock-in/clock-out hours from **ADP RUN's Time Tracker** (a.k.a. Timekeeping Plus), specifically the left-sidebar **Time > Timecards** page.

**Status:** scaffold only. To be implemented in BHAGA milestone M2.

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
2. Open `https://runpayroll.adp.com` in a Playwright session
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
