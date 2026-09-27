# ADP availability + open shifts spike — Issue #337 (2026-09-27)

Read-only Playwright session against ADP RUN Team Schedule ("Manage Schedules",
`iframe[name="timePartnerFrame"]`), weeks of Sep 21 → Oct 18, 2026. Login via
`runner.adp_session` (Keychain creds, Slack OTP). Only the next-week chevron was
clicked. Raw captures stay local under `extracted/spike-adp-availability/`.

## How staff enter availability (ADP docs)
- Employees: ADP Mobile → More → Schedule → **My Unavailability → Add unavailability**
  (one-off or repeating, e.g. a class).
- Managers: Team Schedule grid → `+` on a cell → **Create unavailability** (Repeat dropdown).
- Unavailability submitted by employees arrives as a request type under
  **Pending requests** (`pwc-schedule-request-availability-unavailability-timeline`).

## What Palmetto actually has
| Signal | Finding |
|---|---|
| Unavailability blocks in the grid | **None** in any of the 4 weeks |
| Pending requests | 2 (type not opened) |
| Published schedule horizon | ~2 weeks ahead (through Oct 11); Oct 12 week empty |
| Open Shifts row | In use: week of Sep 28 = 6 shifts / 40 h (Tue 1, Thu 1, Sat 2 w/ 1 claim pending, Sun 2); week of Oct 5 = 7 |
| Open-shift states | `Drafts: N · Published: N · Claims Pending: N` per day; header **Publish drafts (N)** |

## Implications for scheduling
1. **ADP availability is effectively empty** — the draft cannot rely on it yet. Either
   staff start entering *My Unavailability* (then scrape Pending requests + grid), or
   availability is captured in the console. Until then the draft uses sample availability.
2. **Existing ADP open shifts must count as coverage** — otherwise the draft re-drafts
   gaps an open shift already covers. The scraper currently skips the Open Shifts row
   (`SCHEDULE_EMPLOYEE_EXTRACT_JS` `/Open Shifts/`); per-shift times need the day-cell
   detail (the grid row only shows counts).
3. **Finalize can write drafts, not publish**: ADP's open-shift flow has *Save draft* vs
   *Publish*, so Finalize can land ADP drafts for operator review before publishing.
