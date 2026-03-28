# Tax Return Profile Schema

When CHITRA reads a prior-year tax return (extracted PDF text), it produces a structured JSON profile following this schema. This profile drives everything: the document registry, folder structure, checklist, and gap questionnaire for the next year.

## How to Use

1. Extract text from the tax return PDF (use `skills/pdf/extract.py`)
2. Feed the extracted text to CHITRA with this instruction:
   > "Parse this tax return text into a profile JSON following the schema in `return-profile.schema.md`. Extract every issuer, entity, amount, and form. Do not skip any schedule or attachment."
3. CHITRA produces the profile JSON
4. Run `derive_registry_from_return.py --profile <profile.json>` to generate the next-year registry

## Schema

```json
{
  "taxYear": 2024,
  "filingStatus": "Married Filing Jointly | Single | Head of Household | ...",

  "income": {
    "totalIncome": 0,
    "agi": 0,
    "taxableIncome": 0,

    "wages": {
      "total": 0,
      "employers": [
        {
          "name": "Employer legal name as shown on W-2",
          "ein": "XX-XXXXXXX (if visible)",
          "for": "Taxpayer | Spouse",
          "wages": 0,
          "federalWithholding": 0,
          "stateWithholding": 0,
          "state": "XX"
        }
      ]
    },

    "interest": {
      "total": 0,
      "sources": [
        { "payer": "Institution name", "account": "last 4 if visible", "amount": 0 }
      ]
    },

    "dividends": {
      "ordinary": 0,
      "qualified": 0,
      "sources": [
        { "payer": "Institution name", "amount": 0 }
      ]
    },

    "capitalGains": {
      "total": 0,
      "shortTerm": {
        "total": 0,
        "transactions": [
          { "description": "Broker name (Box letter)", "proceeds": 0, "costBasis": 0, "gainLoss": 0 }
        ]
      },
      "longTerm": {
        "total": 0,
        "transactions": [
          { "description": "Broker name (Box letter)", "proceeds": 0, "costBasis": 0, "gainLoss": 0 }
        ]
      }
    },

    "businessIncome": {
      "businessName": "",
      "ein": "",
      "activity": "description of business",
      "accountingMethod": "Cash | Accrual",
      "grossReceipts": 0,
      "netLoss": 0,
      "materialParticipation": true
    },

    "rentalRealEstate": {
      "properties": [
        {
          "address": "Full street address",
          "type": "Single Family | Multi-Family | ...",
          "fairRentalDays": 0,
          "personalUseDays": 0,
          "rentReceived": 0,
          "mortgageLender": "Lender name",
          "expenses": {
            "insurance": 0,
            "managementFees": 0,
            "mortgageInterest": 0,
            "repairs": 0,
            "taxes": 0,
            "depreciation": 0,
            "totalExpenses": 0
          },
          "netLoss": 0
        }
      ]
    },

    "partnerships": [
      {
        "name": "Entity legal name",
        "ein": "XX-XXXXXXX",
        "type": "Partnership | S-Corp",
        "netIncome": 0,
        "rentalRealEstateLoss": 0,
        "note": "any context"
      }
    ]
  },

  "deductions": {
    "type": "Itemized | Standard",
    "totalItemized": 0,
    "taxesPaid": {
      "stateAndLocalIncomeTaxes": 0,
      "realEstateTaxes": 0,
      "saltCap": 10000
    },
    "mortgageInterest": {
      "total": 0,
      "lender": "Lender name for primary residence"
    },
    "charitableContributions": {
      "total": 0,
      "cashContributions": [
        { "organization": "Org name", "amount": 0 }
      ]
    }
  },

  "credits": {
    "note": "List any credits claimed"
  },

  "taxComputation": {
    "regularTax": 0,
    "amt": 0,
    "totalTax": 0
  },

  "payments": {
    "totalPayments": 0,
    "federalWithholdingW2": 0,
    "estimatedPayments": 0,
    "extensionPayment": 0,
    "refund": 0,
    "balanceDue": 0
  },

  "hsa": {
    "coverageType": "Self-only | Family",
    "beneficiary": "Name",
    "employerContribution": 0,
    "personalContribution": 0
  },

  "passiveActivityLosses": {
    "totalSuspended": 0,
    "details": [
      { "activity": "Name", "carryoverToNextYear": 0 }
    ]
  },

  "carryoversToNextYear": {
    "passiveActivityLosses": {},
    "minimumTaxCredit": 0,
    "charitableContributions": 0,
    "capitalLossCarryforward": 0
  },

  "formsAndSchedulesFiled": [
    "Form 1040",
    "Schedule A",
    "..."
  ]
}
```

## Parsing Instructions for CHITRA

When reading a tax return PDF text:

1. **Start with Form 1040** — get filing status, total income, AGI, taxable income, total tax, payments, refund/balance due
2. **Schedule B** — list every interest and dividend payer with amounts
3. **Schedule C** — business name, gross receipts, expenses, net income/loss
4. **Schedule D + Form 8949** — capital gains by broker, short-term vs long-term
5. **Schedule E Page 1** — rental properties with addresses, income, expenses, lenders
6. **Schedule E Page 2** — partnerships and S-corps with names, EINs, income/loss
7. **Schedule A** — itemized deductions: SALT, mortgage interest (with lender), charitable
8. **Form 8889** — HSA details
9. **Form 6251** — AMT computation
10. **Form 8582** — passive activity loss details and carryovers
11. **K-1 attachments** — partnership names, EINs, income types
12. **State returns** — note which states were filed and key line items

For each issuer/entity found, record the **exact legal name** as printed on the return — this is what will appear on next year's documents.
