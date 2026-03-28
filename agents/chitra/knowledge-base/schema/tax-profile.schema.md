# Tax profile JSON schema (`profile-{year}.json`)

This document describes the intended structure of the annual tax profile file used by this repository. It is **documentation only**; validators may be added separately.

**File naming:** `profile-{taxYear}.json` (for example `profile-2025.json`), where `{taxYear}` matches the `taxYear` field inside the document.

**Conventions:**

- Monetary amounts are stored in the same currency as the return (typically USD) unless noted.
- All examples below use **placeholder** labels and numbers (no real names, addresses, account identifiers, or factual dollar amounts).

---

## Top-level shape

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `taxYear` | `number` (integer) | yes | Calendar tax year this profile represents. |
| `income` | `object` | yes | Income items and supporting detail. |
| `deductions` | `object` | yes | Deductions and itemized components. |
| `credits` | `object` | yes | Nonrefundable and refundable credits (structure is evolving; see [Credits](#credits)). |
| `taxComputation` | `object` | yes | Computed tax, AMT, surtaxes, rates, and brackets. |
| `payments` | `object` | yes | Withholding, estimated/extension payments, totals, refund or balance due. |
| `hsa` | `object` | yes | HSA limits, funding, and distributions for the year. |
| `passiveActivityLosses` | `object` | yes | PAL totals and per-activity detail. |
| `carryoversToNextYear` | `object` | yes | Amounts that carry forward (or are tracked for next year’s profile). |
| `formsAndSchedulesFiled` | `string[]` | yes | Checklist of forms and schedules filed with the return (completeness aid). |

```json
{
  "taxYear": 0,
  "income": {},
  "deductions": {},
  "credits": {},
  "taxComputation": {},
  "payments": {},
  "hsa": {},
  "passiveActivityLosses": {},
  "carryoversToNextYear": {},
  "formsAndSchedulesFiled": []
}
```

---

## `income`

| Path | Type | Description |
| --- | --- | --- |
| `income.wages.employers` | `object[]` | One object per W-2 employer (wages, withholdings, optional display labels). |
| `income.interest.sources` | `object[]` | Interest payers and amounts (1099-INT style detail as needed). |
| `income.dividends` | `object` | Ordinary vs qualified (and any other splits your workflow needs). |
| `income.capitalGains.shortTerm` | `object` | Short-term totals and `transactions[]`. |
| `income.capitalGains.longTerm` | `object` | Long-term totals and `transactions`. |
| `income.businessIncome` | `object` | Schedule C–style sole proprietorship (gross receipts, expenses, net, participation flags). |
| `income.rentalRealEstate.properties` | `object[]` | Per-rental property income, expenses, and net (Schedule E alignment). |
| `income.partnerships` | `object[]` | K-1 partnership (and similar) items aggregated or per entity. |

### `wages.employers[]`

| Field | Type | Description |
| --- | --- | --- |
| `employerLabel` | `string` | Generic label for the employer (e.g. `"Employer A"`). |
| `wages` | `number` | Box 1 wages. |
| `federalWithholding` | `number` | Federal income tax withheld (Box 2). |
| `stateWithholding` | `number` | Optional; state tax withheld if tracked. |
| `ficaWithholding` | `number` | Optional; Social Security withheld. |
| `medicareWithholding` | `number` | Optional; Medicare withheld. |
| `additionalFields` | `object` | Optional map for other boxes (retirement, etc.) without baking in a fixed schema here. |

### `interest.sources[]`

| Field | Type | Description |
| --- | --- | --- |
| `payerLabel` | `string` | Institution or payer description. |
| `amount` | `number` | Taxable interest for this source. |
| `accountLast4` | `string` | Optional; last four characters only if you track accounts (never full numbers). |

### `capitalGains.shortTerm` / `longTerm`

| Field | Type | Description |
| --- | --- | --- |
| `total` | `number` | Net short-term or long-term gain/loss for the bucket. |
| `transactions` | `object[]` | Line-item sales (proceeds, cost basis, adjustments, gain/loss). |

Example transaction fields: `description`, `proceeds`, `costBasis`, `adjustments`, `gainLoss` (all placeholders in examples).

### `businessIncome` (Schedule C)

Represent gross receipts, expense categories, net profit or loss, accounting method, and material participation / at-risk flags as your return requires. Keep entity names generic in stored JSON (e.g. `"Business activity label"`).

### `rentalRealEstate.properties[]`

Per property: identifier label, income and expense breakdown, net rental income/loss, and any notes needed for passive activity grouping (see [Passive activity losses](#passiveactivitylosses)).

### `partnerships[]`

Per K-1: entity label, ordinary income/loss, interest, dividends, capital gains allocations, and other boxes you track—using generic entity names only.

```json
{
  "income": {
    "wages": {
      "employers": [
        {
          "employerLabel": "Employer A",
          "wages": 0,
          "federalWithholding": 0,
          "ficaWithholding": 0,
          "medicareWithholding": 0
        }
      ]
    },
    "interest": {
      "sources": [{ "payerLabel": "Bank placeholder", "amount": 0 }]
    },
    "dividends": { "ordinary": 0, "qualified": 0 },
    "capitalGains": {
      "shortTerm": {
        "total": 0,
        "transactions": [
          {
            "description": "Broker lot placeholder",
            "proceeds": 0,
            "costBasis": 0,
            "adjustments": 0,
            "gainLoss": 0
          }
        ]
      },
      "longTerm": {
        "total": 0,
        "transactions": []
      }
    },
    "businessIncome": {
      "activityLabel": "Schedule C activity",
      "grossReceipts": 0,
      "netProfitOrLoss": 0
    },
    "rentalRealEstate": {
      "properties": [{ "propertyLabel": "Rental unit placeholder", "netRentalIncome": 0 }]
    },
    "partnerships": [{ "entityLabel": "Partnership placeholder", "ordinaryIncome": 0 }]
  }
}
```

---

## `deductions`

| Field | Type | Description |
| --- | --- | --- |
| `totalItemized` | `number` | Total itemized deductions after limits (if itemizing). |
| `taxesPaid` | `object` | State/local/property and SALT cap mechanics (see below). |
| `mortgageInterest` | `object` | Home mortgage interest; may split personal vs rental allocation. |
| `investmentInterest` | `number` | Form 4952 investment interest deduction. |
| `charitableContributions` | `object` | Cash vs noncash splits as needed; totals by category. |

### `taxesPaid` and the SALT cap

Document explicitly:

- **State and local taxes** subject to the federal **SALT deduction cap** (a fixed dollar limit that changes only when law or inflation adjustments update it—verify each year from official guidance).
- **Mechanics:** sum eligible state/local income (or sales) taxes and property taxes, then apply the annual cap to the **combined** SALT amount for the filing status used. Any excess is **not** deductible for federal itemized purposes (may still matter for state returns—track separately if needed).

Suggested shape:

| Field | Type | Description |
| --- | --- | --- |
| `stateAndLocalIncomeOrSales` | `number` | Eligible amount before cap. |
| `realPropertyTaxes` | `number` | Eligible property taxes included in SALT bucket. |
| `saltCapApplied` | `number` | Amount actually allowed after cap (should not exceed the statutory cap). |
| `saltDisallowedExcess` | `number` | Portion over the cap (for audit trail). |

### `mortgageInterest` and rental allocation

- **Personal residence:** interest reported on Schedule A (subject to acquisition debt limits and other rules).
- **Rental allocation:** when debt secures both a personal and rental use property, or when interest must be split between Schedule A and Schedule E, store **separate sub-objects** or labeled amounts (e.g. `personalPortion`, `rentalPortion`) so the allocation is explicit and reproducible.

```json
{
  "deductions": {
    "totalItemized": 0,
    "taxesPaid": {
      "stateAndLocalIncomeOrSales": 0,
      "realPropertyTaxes": 0,
      "saltCapApplied": 0,
      "saltDisallowedExcess": 0
    },
    "mortgageInterest": {
      "personalPortion": 0,
      "rentalPortion": 0,
      "notes": "Allocation method placeholder only."
    },
    "investmentInterest": 0,
    "charitableContributions": { "cash": 0, "noncash": 0 }
  }
}
```

---

## `credits`

**Design note:** Credit sections are often under-specified in practice; many returns mix Form 1040 lines, worksheets, and carryovers. Prefer **explicit slots** for nonrefundable vs refundable credits so tooling can sum and order them correctly (nonrefundable cannot reduce tax below zero; refundable can increase refund).

| Area | Type | Description |
| --- | --- | --- |
| `nonrefundable` | `object` | Named credits with amounts (child/dependent, education, foreign tax, etc.). |
| `refundable` | `object` | Credits paid as refund (EITC, additional child tax credit, etc.). |
| `orderingNotes` | `string` | Optional free text describing worksheet order or limitations (no PII). |

```json
{
  "credits": {
    "nonrefundable": { "childTaxCredit": 0, "foreignTaxCredit": 0 },
    "refundable": { "additionalChildTaxCredit": 0 },
    "orderingNotes": "Extend keys as needed; validate against Form 1040 ordering for the tax year."
  }
}
```

---

## `taxComputation`

| Field | Type | Description |
| --- | --- | --- |
| `regularTax` | `number` | Tax on ordinary brackets before credits (naming may align with Form 1040 “tax” line context). |
| `amt` | `number` | Alternative minimum tax after AMT credits as applicable. |
| `amtDetails` | `object` | Inputs for AMT (e.g. ISO exercise excess, AMTI components). |
| `additionalMedicareTax` | `number` | Additional Medicare Tax under IRC Section 3101(b)(2) (verify line mapping yearly). |
| `NIIT` | `number` | Net Investment Income Tax (Form 8960). |
| `totalTax` | `number` | Total tax before payments (after credits per form instructions). |
| `effectiveRate` | `number` | Typically `totalTax / AGI` or `totalTax / taxableIncome`—document which denominator you use in tooling. |
| `taxBracket` | `object` | Marginal ordinary rate and bracket boundaries used (placeholders for table-driven values). |

### `amtDetails` (illustrative)

| Field | Type | Description |
| --- | --- | --- |
| `isoExerciseSpreadExcess` | `number` | ISO bargain element and adjustments relevant to AMT (per Form 6251). |
| `alternativeMinimumTaxableIncome` | `number` | AMTI (before exemption and tentative minimum tax). |
| `otherAdjustments` | `object` | Optional map for other AMT preference items. |

```json
{
  "taxComputation": {
    "regularTax": 0,
    "amt": 0,
    "amtDetails": {
      "isoExerciseSpreadExcess": 0,
      "alternativeMinimumTaxableIncome": 0,
      "otherAdjustments": {}
    },
    "additionalMedicareTax": 0,
    "NIIT": 0,
    "totalTax": 0,
    "effectiveRate": 0,
    "taxBracket": { "marginalOrdinaryRate": 0, "bracketLabel": "Placeholder" }
  }
}
```

---

## `payments`

| Field | Type | Description |
| --- | --- | --- |
| `federalWithholdingW2` | `number` | Federal withholding from W-2 (and similar wages). |
| `additionalMedicareWithholding` | `number` | If tracked separately from generic Medicare withholding. |
| `extensionPayment` | `number` | Amount paid with extension request, if any. |
| `totalPayments` | `number` | Sum of withholding, estimated payments, extension, and other payments applied. |
| `refund` | `number` | Overpayment to be refunded (positive) or amount owed (negative), per your convention—state clearly in tooling. |

```json
{
  "payments": {
    "federalWithholdingW2": 0,
    "additionalMedicareWithholding": 0,
    "extensionPayment": 0,
    "totalPayments": 0,
    "refund": 0
  }
}
```

---

## `hsa`

| Field | Type | Description |
| --- | --- | --- |
| `limits` | `object` | Annual contribution limit for coverage type (self-only vs family) and catch-up if applicable. |
| `contributions` | `object` | Employer, employee, and rollover contributions as tracked. |
| `distributions` | `object` | Qualified vs nonqualified; Form 8889 alignment. |

```json
{
  "hsa": {
    "limits": { "annualContributionLimit": 0, "coverageType": "self_only_or_family" },
    "contributions": { "employer": 0, "employee": 0, "rollover": 0 },
    "distributions": { "qualifiedMedical": 0, "other": 0 }
  }
}
```

---

## `passiveActivityLosses`

| Field | Type | Description |
| --- | --- | --- |
| `totals` | `object` | Aggregate passive income, losses allowed, suspended losses. |
| `details` | `object[]` | One entry per rental, partnership, or other passive activity. |

Per-activity `details[]` should link to the same labels used under `income.rentalRealEstate` / `income.partnerships` where applicable (generic IDs only).

```json
{
  "passiveActivityLosses": {
    "totals": { "passiveIncome": 0, "passiveLossAllowed": 0, "suspendedLoss": 0 },
    "details": [
      {
        "activityId": "activity-placeholder-1",
        "activityType": "rental",
        "currentYearLoss": 0,
        "allowedAgainstIncome": 0,
        "suspendedCarryforward": 0
      }
    ]
  }
}
```

---

## `carryoversToNextYear`

Track opening balances for the **next** profile year after the return is filed.

| Area | Type | Description |
| --- | --- | --- |
| `passiveActivityLosses` | `object` | Buckets by activity or type (rental vs partnership) with suspended amounts. |
| `minimumTaxCredit` | `number` | AMT credit carryforward (Form 8801 context). |
| `tentativeAdditionalMTC` | `number` | If you track tentative minimum tax credit components separately. |
| `stateCredits` | `object` | State credit carryforwards (generic keys by state code if needed). |
| `qbiLossCarryforward` | `object` | Section 199A loss carryforwards **split by vintage** (year originated) when multiple years contribute. |

```json
{
  "carryoversToNextYear": {
    "passiveActivityLosses": {
      "byActivity": { "activity-placeholder-1": 0 }
    },
    "minimumTaxCredit": 0,
    "tentativeAdditionalMTC": 0,
    "stateCredits": { "STATE": 0 },
    "qbiLossCarryforward": {
      "byVintage": { "2023": 0, "2024": 0 }
    }
  }
}
```

---

## `formsAndSchedulesFiled`

A **string array** checklist for completeness (not a substitute for the PDF return).

Example entries (generic):

```json
{
  "formsAndSchedulesFiled": [
    "Form 1040",
    "Schedule 1 (Additional Income and Adjustments)",
    "Schedule A",
    "Schedule C",
    "Schedule E",
    "Form 6251",
    "Form 8960",
    "Form 8889"
  ]
```

---

## Year-rollover procedure

When starting a new tax year from a **filed** prior-year profile:

1. **Clone** the prior `profile-{year}.json` from the **as-filed** snapshot (or locked copy), not from draft experiments.
2. **Reset income** for the new year: set wages, interest, dividends, capital gain transactions, business and rental current-year amounts to **zero** or empty arrays as appropriate. Remove prior-year-only line items.
3. **Preserve carryovers** as **starting balances**: copy `carryoversToNextYear` from the filed return into the new file’s `carryoversToNextYear` (or merge into opening fields—be consistent). PAL and QBI vintage buckets should roll forward with updated math only where the law requires.
4. **Bump** `taxYear` to the new calendar year and rename the file to `profile-{newYear}.json`.
5. **Update bracket, exemption, and standard-deduction tables** (and SALT cap, HSA limits, etc.) from **IRS Revenue Procedures** and other official sources for the new year—do not carry numeric thresholds forward silently without verification.

After rollover, validate `formsAndSchedulesFiled` against what you actually intend to file for the new year (often starts empty or minimal until return prep progresses).

---

## Versioning

This Markdown file is the human-readable contract. When the JSON shape changes, update this document and bump a `schemaVersion` field **if** you add one at the root of `profile-{year}.json` in a future revision (optional; not required by this spec).
