# Estimates data model (three-layer architecture)

This document describes how **tax estimates** are represented as three coordinated layers: prior-year actuals, professional projections, and a working scenario with an audit trail. Examples use **placeholder values only**—suitable for open-source docs and tests.

## Overview

| Layer | Role |
|-------|------|
| **baseline** | Pull-through from the **prior-year filed return** (actual line items or summarized actuals). Anchor for “what happened last year.” |
| **professionalEstimate** | **CPA / firm** projection, optionally with named **scenarios** (e.g. conservative vs base). |
| **current** | **Working scenario** maintained by **CHITRA** (the automation/agent layer), with explicit **`adjustments[]`** explaining how it diverges from baseline and/or professional inputs. |

Flow in words: baseline → informs professionalEstimate → current applies incremental, dated changes with reasons.

---

## 1. `baseline`

**Source of truth**: Filed prior-year return (or locked “as-filed” extract).

**Intent**: Immutable or rarely changed snapshot used for YoY comparison and safe-harbor heuristics that reference **prior year tax** (conceptually—not every implementation stores every IRS line).

**Typical content**:

- Filing status, dependents count (if modeled).
- Key income buckets, deduction buckets, credits (as your schema defines).
- Metadata: tax year, source (`filed_2024_return`), last verified date.

---

## 2. `professionalEstimate`

**Source of truth**: CPA or firm deliverable (spreadsheet, PDF summary, or exported JSON).

**Intent**: Official projection with optional **scenarios** (e.g. `base`, `high_income`, `sale_of_property`). Each scenario holds the firm’s numbers for the **current** planning year.

**Typical content**:

- Scenario id and label.
- Per-line or per-category amounts aligned with your internal keys.
- Optional notes or assumptions reference (non-PII in shared repos).

---

## 3. `current`

**Source of truth**: CHITRA-maintained **working copy** for the active planning year.

**Intent**: The numbers the UI or agent acts on **right now**, plus **`adjustments[]`** so every change is explainable.

**`adjustments[]` — audit trail**

Each entry records **why** the working estimate changed.

| Field | Type | Description |
|-------|------|-------------|
| `date` | string (ISO 8601 date) | When the adjustment was applied or recognized. |
| `description` | string | Human-readable summary of the change. |
| `source` | string | Origin: e.g. `user_edit`, `import_1099`, `cpa_sync`, `rule_engine`, `chat_session`. |

---

## JSON structure examples (placeholders)

### Minimal three-layer document

```json
{
  "taxYear": 2025,
  "baseline": {
    "sourceTaxYear": 2024,
    "source": "prior_filed_return",
    "lastVerified": "2025-03-01",
    "summary": {
      "agiBucket": "PLACEHOLDER_AGI",
      "ordinaryIncomeApprox": 0,
      "withholdingApprox": 0
    }
  },
  "professionalEstimate": {
    "provider": "EXAMPLE_FIRM",
    "asOf": "2025-02-15",
    "scenarios": [
      {
        "id": "base",
        "label": "Base case",
        "summary": {
          "projectedTaxDue": 0,
          "projectedBalanceDue": 0
        }
      }
    ],
    "activeScenarioId": "base"
  },
  "current": {
    "asOf": "2025-03-10",
    "summary": {
      "projectedTaxDue": 0,
      "projectedBalanceDue": 0
    },
    "adjustments": [
      {
        "date": "2025-03-05",
        "description": "Updated withholding after employer correction (generic).",
        "source": "user_edit"
      },
      {
        "date": "2025-03-08",
        "description": "Imported dividend total from brokerage export.",
        "source": "import_1099"
      }
    ]
  }
}
```

### `adjustments[]` only (illustrative)

```json
{
  "adjustments": [
    {
      "date": "2025-01-20",
      "description": "Initial copy from professional base scenario.",
      "source": "cpa_sync"
    },
    {
      "date": "2025-02-01",
      "description": "Added estimated state payment.",
      "source": "user_edit"
    }
  ]
}
```

---

## Field-by-field documentation (extensible `summary` objects)

Exact keys inside `baseline.summary`, `professionalEstimate.scenarios[].summary`, and `current.summary` depend on the product. Document **your** keys in code or a machine-readable schema; the table below lists **common conceptual fields** (all numeric examples are placeholders).

| Path | Meaning |
|------|---------|
| `taxYear` | Planning year for `current` / professional estimate. |
| `baseline.sourceTaxYear` | Year of the filed return used as baseline. |
| `baseline.source` | How baseline was loaded (`prior_filed_return`, `manual`, etc.). |
| `baseline.lastVerified` | Last time baseline matched a trusted document. |
| `professionalEstimate.provider` | Opaque label for firm or tool (no real contact data in public repos). |
| `professionalEstimate.asOf` | Date the professional snapshot reflects. |
| `professionalEstimate.scenarios` | Array of named projections. |
| `professionalEstimate.activeScenarioId` | Which scenario is the default for comparison to `current`. |
| `current.asOf` | Last update to working numbers. |
| `current.summary` | Same shape as scenario summaries for easy diffing. |
| `current.adjustments[]` | Ordered audit log; append-only in principle. |

---

## Known gaps (implementation awareness)

These are **documented limitations** of the current design—not bugs in every deployment, but risks to track.

1. **Stale zeros vs rich notes**  
   The `current` object can still show **placeholder zeros** in `summary` while **notes or chat context** describe real activity. Consumers must not assume `summary` is complete without cross-checking narrative fields or source documents.

2. **No first-class quarterly safe-harbor math object**  
   Estimated tax **safe harbor** rules (e.g. prior-year tax vs current-year annualized) are **not** represented as a dedicated structured object in this three-layer model. If you need quarterly planning, add an explicit `safeHarbor` or `estimatedPaymentsPlan` block with its own assumptions and dates.

3. **Charity toggle not driving `current`**  
   A **charitable giving** on/off or amount **toggle** may exist in UI or config but is **not** a first-class boolean (or amount) that **directly drives** `current.summary` unless you wire it. Without that linkage, `current` can omit charitable impact even when the user believes it is “on.”

---

## Design notes

- **Order of precedence** for “what should we show?” is product-specific; commonly: `current` for UI, diff `current` vs `professionalEstimate` for drift, diff vs `baseline` for YoY.
- **Immutability**: Treat `baseline` as read-only after lock; mutate only `current` and append to `adjustments[]`.
- **Privacy**: Do not store raw SSN, account numbers, or exact PII in estimate JSON intended for logs or public examples.
