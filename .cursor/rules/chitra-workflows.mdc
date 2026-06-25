---
description: CHITRA workflow templates -- loaded on demand, NOT every turn
globs: []
alwaysApply: false
---

# CHITRA Workflows (read only when needed)

## CPA Email Template

```
Subject: [{Taxpayer Names}] {Year} Tax Filing – Documents & Questions

Dear {CPA Name},

Here is our current status for the {Year} tax year filing (Tax Year {Year}, {Filing Status}).

## Documents Collected ([X] of [Y])
[List received documents with filenames]

## Documents Still Pending ([Z] remaining)
[List by category with expected availability dates]
[Flag any overdue items]

## Life Changes in {Year}
[From changes.json]

## Questions & Discussion Items
[From cpaQuestions in document-registry.json -- overall then by section]

## Key Carryovers from Prior Year
[From profile-{prior_year}.json carryoversToNextYear section]
- Passive Activity Losses: $[X] (list each activity)
- AMT Credit (Form 8801): $[X]
- QBI Loss Carryforward: $[X]

## Estimated Tax Situation
[From estimates.json]

Best regards,
{Taxpayer Primary Name}
```

## Change Management Workflow

When user reports a life change:
1. Classify (job, income, deduction, investment, property, life event, entity)
2. Ask clarifying questions if needed
3. Update `{year}/changes.json` with structured entry
4. Update `{year}/estimates.json` with adjustment
5. Update `document-registry.json` (new docs, status changes, questions)
6. Rebuild Google Sheet (`scripts/populate_sheet.py`)
7. Confirm with user

## Tax Estimation Methodology

Start from prior year actuals (in `profile-{prior_year}.json`), layer adjustments:
1. Income: wages, capital gains, rental, business
2. Deductions: standard vs itemized, SALT cap, charitable, mortgage
3. Tax: current year MFJ brackets (update from IRS Rev. Proc. annually)
4. AMT exemption: current year MFJ amount (check IRS Rev. Proc.)
5. Credits: minimum tax credit carryforward and other applicable credits
6. Compare to payments; flag underpayment risk if >$1K owed

## Tax Calendar (Generic)

| Relative Date | Event |
|------|-------|
| Jan 15 (year+1) | Q4 estimated payment due |
| Jan 31 (year+1) | W-2s and most 1099s due |
| Feb 15 (year+1) | 1099-B from brokerages |
| Mar 15 (year+1) | K-1s due (or extension) |
| Apr 15 (year+1) | Federal return due (or Form 4868) |
| Oct 15 (year+1) | Extended return due |

## ISO Sheet Row Convention

One row per disposition. Col H = Col K for each sale row. Partial exercise: split into sale rows (H=K=sold) + Still Held row (H=K=remaining). Same A-E metadata on each split. See `iso-tracker.json` for full details.

## Query Routing

- Missing docs → sync first, filter `document-registry.json` by status
- Mark received → update registry + Sheet
- Upload notification → scan Drive folder, inspect, update registry + Sheet
- Tax estimate → read `{year}/estimates.json`
- CPA email → use template above
- Life change → use change management workflow
- Prior year questions → read `profile-{prior_year}.json`
