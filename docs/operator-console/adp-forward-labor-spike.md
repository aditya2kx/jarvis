# ADP forward labor spike — Issue #166 (2026-07-14)

Live Playwright session against ADP RUN (Palmetto). Fixtures under
`skills/adp_run_automation/testdata/schedule_employee_grid_spike.json` and
screenshots under `extracted/spike-adp-forward-labor/` (local only).

## A — Per-employee forward schedule

### Where it lives
- Nav: Home → **Time** (hydrates TEMPUS anchors) → `#TEMPUS_WEEKLY_SCHEDULE` →
  Manage Schedules grid in `iframe[name="timePartnerFrame"]`.
- Horizon: **current + next week** (confirmed Jul 13–19 and Jul 20–26, 2026).
- Footer day totals (existing scrape): e.g. `14 Employees 247:00 Hrs` with 7 day cells.

### Per-employee DOM (scrapable)
| Element | Role |
|---|---|
| `.worker-name` | Employee display name (`Last, First`) |
| `team-schedule-total` (no "Employees") | Per-employee **week** total (`19:00 Hrs`) |
| `team-schedule-calendar-day` | Day cell (may omit empty days) |
| `schedule-shift-range` | Shift window text (`1:30 PM - 8:30 PM`) |
| `.day-cell.column-header` | Weekday headers — align cells by **bounding-box X** |

Empty days often lack a `team-schedule-calendar-day` node, so **ordinal dayIndex ≠ weekday**.
Date assignment must match each cell’s X center to the nearest column header, then
`week_start + weekday_offset`.

### Pitfalls (fixed 2026-07-14)
- **Shared-grid over-attribution:** climbing past `.calendar-row` into the SECTION
  attaches every employee’s shifts to mid-list names (Tina/Ximena showed 13×
  `week_total`). Scope extract to `.calendar-row` with exactly one `.worker-name`.
- **Virtualization:** mid-list rows hydrate day cells only when scrolled into view
  (`days=0` until then). Runner scrolls each row, extracts one-at-a-time, then
  resets scroll before next-week chevron click.
- **Safety net:** `cap_days_to_week_total` trims day cells when
  `sum(ranges) > 1.2 × week_total` (true shifts appear first).

### Feasibility
**GO.** Extend existing schedule scrape in the same nightly bundle. Backfill = the
2 weeks ADP shows now (no older history in UI). Incremental = nightly MERGE upsert
on `(date, employee_id)` (same pattern as `adp_shifts`).

### Target schema
```sql
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_scheduled_shifts` (
  date                DATE NOT NULL,
  employee_id         STRING NOT NULL,  -- canonical name key (alias map)
  employee_name       STRING,
  scheduled_hours     FLOAT64,          -- decimal, from shift ranges
  shift_ranges_json   STRING,           -- optional audit ["1:30 PM - 8:30 PM"]
  week_start          DATE,
  scraped_at_utc      TIMESTAMP,
  materialized_at_utc TIMESTAMP
);
-- MERGE keys: date, employee_id
```
Keep `adp_scheduled_daily` from footer totals (unchanged). Employee table is additive.

---

## B — Employer burden (all-in)

### Where it lives
- **Taxes** top-nav → **Tax reports** → report **Payroll Liability** (opens with data;
  no multi-step date wizard required for latest payroll).
- Sample (check date **2026-07-17**, biweekly Payroll 1):

| Line | EE withheld | ER contrib |
|---|---:|---:|
| Social Security | 712.33 | **712.31** |
| Medicare | 166.59 | **166.58** |
| FUTA (0.6%) | — | **40.66** |
| TX SUI (2.7%) | — | **211.07** |
| **ER tax total** | | **1,130.62** |
| Pay-by-Pay (WC-ish) | | 43.08 |

Approx wage base from SS EE / 0.062 ≈ **$11,489** →
`ER / wages ≈ 9.8%`; with Pay-by-Pay ≈ **10.2%**.

- **Statements of Deposit**: wizard stuck on “Clear selections / Next” without period
  picks — defer.
- **Tax profile** (`NG_COMPANY_TAX_INFORMATION`): EIN / deposit schedule only — not $.
- **Workers' Comp** nav: certificate UX, not period $ (Pay-by-Pay appears on liability).

### Feasibility
**GO** for `Payroll Liability` scrape (JS click `Payroll Liability` / report list).
**Fallback SoT:** `store_config.labor_burden_pct` — seed **0.10** from measured ~10%
(prior research ballpark 0.13 was conservative).

### Target schema
```sql
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_payroll_liability` (
  check_date              DATE NOT NULL,
  payroll_label           STRING,       -- e.g. "7/17/2026 - Payroll 1"
  er_social_security      FLOAT64,
  er_medicare             FLOAT64,
  er_futa                 FLOAT64,
  er_sui                  FLOAT64,
  er_tax_total            FLOAT64,
  pay_by_pay              FLOAT64,      -- workers-comp-ish
  ee_tax_total            FLOAT64,
  approx_ss_wage_base     FLOAT64,      -- ee_ss / 0.062 when present
  effective_burden_pct    FLOAT64,      -- (er_tax_total + pay_by_pay) / wage_base
  scraped_at_utc          TIMESTAMP,
  materialized_at_utc     TIMESTAMP
);
-- MERGE keys: check_date, payroll_label
```

---

## Console showcase (locked)

| Surface | Change |
|---|---|
| `/labor` forward card | Projected PT $ = `Σ emp_scheduled_hours × emp_wage` (fallback avg wage) |
| `/labor` | New **Scheduled hours per person** table (next 14 days / open Period) |
| `/labor` charts | Dashed projected segment for forward scheduled days in Period |
| Home Labor group | Same upgraded math; no per-person grid |
| All-in lines | When `labor_burden_pct > 0` (seed 0.10) |

---

## Incremental sync

| Channel | Backfill | Incremental |
|---|---|---|
| Schedule shifts | One scrape of current+next week | Nightly re-scrape both weeks; MERGE `(date,employee_id)`; delete dates in scraped `week_start`s that disappeared |
| Payroll liability | Last 2–4 closed payrolls via report Edit date range when available | On earnings nights, open latest Payroll Liability and MERGE |

---

## Implementation order (post-spike)

1. `schedule_backend` employee extract + unit tests from redacted fixture  
2. Migration `039_adp_scheduled_shifts.sql` + loader  
3. Extend runner `Schedule-*.json` payload with `employee_rows`  
4. Payroll liability parser + migration `040` + seed `labor_burden_pct=0.10`  
5. Console query/UI + docs lockstep  
