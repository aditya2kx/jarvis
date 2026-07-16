# ADP labor burden research — Issue #166 (2026-07-14)

## Question
Does BHAGA / Operator Console labor cost reflect **hourly wages only**, or **fully-loaded payroll** (employer taxes, insurance, etc.)?

## Findings

### What we cost today (code + BQ)
- Pipeline labor cost = `regular_hours × wage_rate + ot × ot_rate + dt × rate×2`
  (`agents/bhaga/scripts/update_model_sheet.py`).
- `bhaga.adp_earnings` descriptions observed in prod: Regular, Overtime, Credit Card Tips Owed,
  Bonus, Cash tips, Sick, Misc reimbursement — **employee pay lines only**.
- No employer FICA / FUTA / SUTA / workers' comp columns exist in BQ.

### ADP RUN UI (Playwright)
- Confirmed 2026-07-14: **Taxes → Tax reports → Payroll Liability** shows employer
  FICA / FUTA / TX SUI (ER contrib) plus Pay-by-Pay. See
  [`adp-forward-labor-spike.md`](adp-forward-labor-spike.md).
- Sample check date 2026-07-17: ER tax **$1,130.62** + Pay-by-Pay **$43.08** on
  ≈$11,489 SS wage base → **effective burden ≈ 10.2%**.

### Recommendation
| Approach | Verdict |
|---|---|
| Keep warehouse wage-only; console optional all-in | **Ship** |
| Scrape Payroll Liability into `adp_payroll_liability` | **Done** (nightly best-effort) |

**`labor_burden_pct`:** seeded **`0.10`** from measured ~10.2% (earlier 0.13 ballpark was conservative). Unset/`0` → wage-only only.

## Live math spot-check (this week, Chicago, 2026-07-14)
- Completed: 1 day, net sales $710.99, labor $0 (ADP punches not yet on 2026-07-13).
- Forward: 6 days, 221 scheduled hrs, 572 forecast orders.
- Avg PT wage $15.32, AOV $16.19, avg FT $/open-day $136.91.
- Implied projected PT labor% ≈ **34.0%**; projected total ≈ **42.2%**.
