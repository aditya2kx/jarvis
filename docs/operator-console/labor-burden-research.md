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
- Live ADP browser session was **unavailable** this session (`user-playwright-palmetto` →
  `ECONNREFUSED 127.0.0.1:9222`). Research therefore uses BQ evidence + ADP RUN product knowledge:
  employer tax liability lives in RUN's **Tax** / payroll-summary surfaces, not in the
  "Earnings and Hours" report we already scrape for wage rates.

### Recommendation
| Approach | Verdict |
|---|---|
| Keep warehouse wage-only; console optional all-in | **Ship** — matches Issue #166 |
| New ADP Tax Center scrape into BQ | **Follow-up** only if multiplier is too coarse |

**Recommended `labor_burden_pct`:** `0.13` (≈ 7.65% employer FICA + ~2–3% FUTA/SUTA + ~2–3% workers' comp ballpark for TX QSR). Operator sets explicitly:

```text
/bhaga-cloud config set labor_burden_pct 0.13
```

Unset / `0` → console shows wage-only only (no all-in lines). Agent does **not** write this to prod.

## Live math spot-check (this week, Chicago, 2026-07-14)
- Completed: 1 day, net sales $710.99, labor $0 (ADP punches not yet on 2026-07-13).
- Forward: 6 days, 221 scheduled hrs, 572 forecast orders.
- Avg PT wage $15.32, AOV $16.19, avg FT $/open-day $136.91.
- Implied projected PT labor% ≈ **34.0%**; projected total ≈ **42.2%**.
