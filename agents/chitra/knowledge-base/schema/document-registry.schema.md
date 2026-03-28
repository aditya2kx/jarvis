# Document Registry Schema

This document describes the JSON shape used by the **document registry** artifact in the CHITRA workflow. It is intended for open-source publication: all examples use anonymized placeholders (e.g. Taxpayer A, Employer Corp, `$XXX,XXX`).

---

## Purpose

The registry tracks which tax documents are expected for a given year, where they live in Google Drive, extracted facts for automation, operational notes, and questions for a tax professional. **Status** reflects user collection and upload progress—not CPA review or filing state.

---

## Top-level object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `taxYear` | `number` | yes | Calendar or tax year the registry describes (e.g. `2025`). |
| `basedOn` | `string` | yes | Provenance: what sources the registry was built from (e.g. prior return, Drive scan date). Free-form. |
| `lastUpdated` | `string` (ISO 8601 date) | yes | Last manual or automated update to the registry file (e.g. `2026-03-27`). |
| `driveInventoryFile` | `string` | yes | Path (relative to repo root or agreed root) to the Drive scan / inventory JSON produced by tooling. |
| `driveFolderStructure` | `object` | yes | Map of **human-readable folder label** → **Google Drive folder ID** (opaque string). Keys often mirror logical paths; values are API identifiers, not paths. |
| `documents` | `array` | yes | Core list of expected documents; see [Per-document schema](#per-document-schema). |
| `emptyFolders` | `array` of `string` | yes | Logical paths (or labels) that were empty at scan time and may need attention. **These go stale** as soon as files are added—treat as a snapshot, not ground truth. |
| `majorDiscoveries` | `array` of `string` | yes | Short narrative flags for significant findings (e.g. new investment, missing K-1 sponsor). |
| `cpaQuestions` | `object` | yes | Aggregate questions for a CPA: `overall` and `bySection`; see [CPA questions aggregate](#cpa-questions-aggregate). |

### CPA questions aggregate

| Field | Type | Description |
|-------|------|-------------|
| `overall` | `string[]` | Cross-cutting questions not tied to a single document. |
| `bySection` | `object` | Keys are section labels (e.g. income, rental, equity); values are `string[]` questions for that section. |

---

## Per-document schema

Each element of `documents` is an object. Together they form a **discriminated union** on `docType` (and sometimes `category`). Fields below apply to all types unless noted.

### Field reference

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | `number` | yes | Stable identifier. **Not sequential**: gaps are normal after deletions; never reuse IDs. |
| `docType` | `string` | yes | One of the [known document types](#known-document-types-23). |
| `issuer` | `string` | yes | Payer, employer, institution, or sponsor (display string). |
| `for` | `string` | yes | Taxpayer name as used in the registry, or `"Joint"`. |
| `category` | `string` | yes | One of: `Income`, `Deduction`, `Other`. |
| `availabilityMonth` | `string` | yes | Typical availability: `Jan`, `Feb`, `Mar`, `Aug`, `Oct`, or `N/A`. |
| `status` | `string` | yes | See [Status machine](#status-machine). |
| `driveFileId` | `string` | no | Google Drive file ID for single-file documents. |
| `driveFileName` | `string` | no | Display name of the primary file in Drive. |
| `drivePath` | `string` | no | Logical path within Drive (convention: prefer **numbered folder** scheme; see [Known gaps](#known-gaps-and-limitations)). |
| `driveFiles` | `array` | no | Multi-file docs: items `{ "id": string, "name": string, "note": string }`. |
| `driveFolderId` | `string` | no | Rare: upload target folder when the doc is not yet a single file. |
| `extractedData` | `object` | no | Type-specific; see [extractedData by document type](#extracteddata-by-document-type). |
| `notes` | `string` | no | Operational narrative (what was done, file quirks, cross-links). |
| `questions` | `string[]` | no | Per-document questions for the CPA. |
| `contact` | `object` | no | For K-1s and similar: `{ "name", "email", "company", "portal" }` (strings; portal = URL or label). |
| `correspondence` | `array` | no | See [Correspondence tracking](#correspondence-tracking). Items: `{ "date", "from", "summary" }`. |
| `nextFollowUp` | `string` | no | ISO 8601 date for the next follow-up action (often paired with `correspondence`). |

---

## Known document types (23)

Enumerated `docType` values (exact strings used in the registry):

1. `W-2`
2. `Consolidated 1099 (Brokerage)`
3. `Stock Plan Supplement`
4. `Form 1099-MISC (Rental)`
5. `Form 1098 (Mortgage)`
6. `Property Tax Bill`
7. `Homestead Exemption`
8. `Form 1095-C (Health Coverage)`
9. `K-1 (Partnership)`
10. `Bank Transactions (Rental)`
11. `Insurance Policy (Rental)`
12. `Charitable/Donation Instrument`
13. `Tax Estimate (Professional)`
14. `Prior Year Return`
15. `Retirement Account Records (1099-R)`
16. `Student Loan (1098-E)`
17. `HSA Records`
18. `IUL/Insurance Statement`
19. `Schedule C Records (Business)`
20. `State Tax Payment`
21. `Federal Estimated Payment`
22. `Form 3921 (ISO Exercise)`
23. `ISO Disposition Tracker`

---

## extractedData by document type

Shapes below are **canonical** for CHITRA where specified. Other types may omit `extractedData` or use a minimal ad hoc object until standardized.

### W-2

| Key | Type | Description |
|-----|------|-------------|
| `wages` | `number` | Box 1 wages (example: `$XXX,XXX.XX`). |
| `federalWithholding` | `number` | Federal income tax withheld. |
| `ssWages` | `number` | Social Security wages (Box 3). |
| `ssTax` | `number` | Social Security tax withheld (Box 4). |
| `medicareWages` | `number` | Medicare wages (Box 5). |
| `medicareTax` | `number` | Medicare tax withheld (Box 6). |
| `box12Codes` | `object` or structured map | Box 12 codes and amounts (convention project-specific). |
| `state` | `string` | State code or label if applicable. |

### Consolidated 1099 (Brokerage)

| Key | Type | Description |
|-----|------|-------------|
| `accountNumber` | `string` | Masked or last-four style as stored. |
| `preparedDate` | `string` | Statement / tax form date. |
| `formsIncluded` | `string[]` | e.g. `1099-INT`, `1099-DIV`, `1099-B`. |
| `isStockPlan` | `boolean` | Whether equity / ESPP supplements apply. |
| `belowThreshold` | `boolean` | Whether reporting is below filing thresholds (if tracked). |

### K-1 (Partnership)

| Key | Type | Description |
|-----|------|-------------|
| `preparedBy` | `string` | Preparer or partnership name. |
| `taxYearEnding` | `string` | Fiscal year end if not calendar. |
| `datePrepared` | `string` | K-1 issue date. |
| `distributions` | `array` | Line-item or categorized distributions. |
| `totalDistributions` | `number` | Aggregate if summarized. |

### Form 1098 (Mortgage)

| Key | Type | Description |
|-----|------|-------------|
| `mortgageInterest` | `number` | Box 1. |
| `outstandingPrincipal` | `number` | Box 2 / loan balance context. |
| `originationDate` | `string` | Loan origination. |
| `realEstateTaxesPaid` | `number` | If in escrow / reported. |
| `propertyAddress` | `string` | e.g. `123 Main St, City, ST`. |
| `accountNumber` | `string` | Masked account identifier. |

### Property Tax Bill

| Key | Type | Description |
|-----|------|-------------|
| `parcelNumber` | `string` | Assessor parcel ID. |
| `propertyAddress` | `string` | Property location. |
| `assessedValue` | `number` | Assessed value for the bill year. |
| `taxComponents` | `array` | Jurisdiction line items (label + amount). |
| `latePenalty` | `number` | If applicable. |
| `totalTax` | `number` | Total due or paid per bill. |

### Form 1099-MISC (Rental)

| Key | Type | Description |
|-----|------|-------------|
| `rentsBox1` | `number` | Rents in Box 1. |
| `payerTIN` | `string` | Masked TIN / last four if stored. |
| `recipientAddress` | `string` | Recipient on form. |

### Bank Transactions (Rental)

| Key | Type | Description |
|-----|------|-------------|
| `monthlyMortgage` | `number` | Typical monthly mortgage. |
| `monthlyHOA` | `number` | HOA dues. |
| `monthlyManagementFee` | `number` | Property management. |
| `monthlyRent` | `number` | Rent received (if derived here). |
| `propertyTaxPayment` | `number` | Property tax outflow (annual or normalized). |

### Charitable/Donation Instrument

| Key | Type | Description |
|-----|------|-------------|
| `dateExecuted` | `string` | Execution date. |
| `principalAmount` | `number` | Principal or commitment amount. |
| `maturityPayment` | `number` | Scheduled payment. |
| `interestCap` | `number` | Interest cap if applicable. |
| `securedBy` | `string` | Collateral description (generic). |
| `entity` | `string` | Donee or trust name (generic). |

### Tax Estimate (Professional)

| Key | Type | Description |
|-----|------|-------------|
| `preparedBy` | `string` | Firm or preparer. |
| `preparedDate` | `string` | Date of estimate. |
| `scenarios` | `array` | Named scenarios (objects or labels; structure project-specific). |

### Form 1095-C (Health Coverage)

| Key | Type | Description |
|-----|------|-------------|
| `coverageCode` | `string` | Line 14 code. |
| `employeeCost` | `number` | Employee share (e.g. Box 12 or worksheet). |
| `safeHarborCode` | `string` | Affordability / safe harbor indicator if used. |

### Homestead Exemption

| Key | Type | Description |
|-----|------|-------------|
| `exemptionType` | `string` | e.g. general, over-65, disability. |
| `taxYear` | `number` | Year the exemption applies to. |
| `approvedDate` | `string` | Approval date. |
| `applicationId` | `string` | Internal reference ID (non-sensitive). |

### Insurance Policy (Rental)

| Key | Type | Description |
|-----|------|-------------|
| `policies` | `array` | Items: `{ "policyNumber", "period", "coverage", "premium" }` (strings/numbers as appropriate). |
| `proratedDeduction` | `number` | Deduction amount allocated to tax year if computed. |

### Property tax (primary residence) — supplemental shape

Used when distinguishing primary-home property tax from a generic **Property Tax Bill** (e.g. jurisdiction split for homestead context).

| Key | Type | Description |
|-----|------|-------------|
| `jurisdictionSplits` | `array` | School, county, city, etc. with amounts. |
| `paidAmount` | `number` | Total paid in scope. |
| `exemptionStatus` | `string` | e.g. active, pending, none. |

### Types without a fixed shape in CHITRA v1

The following `docType` values may carry `extractedData` as needed or omit it until a schema is added:

`Stock Plan Supplement`, `Prior Year Return`, `Retirement Account Records (1099-R)`, `Student Loan (1098-E)`, `HSA Records`, `IUL/Insurance Statement`, `Schedule C Records (Business)`, `State Tax Payment`, `Federal Estimated Payment`, `Form 3921 (ISO Exercise)`, `ISO Disposition Tracker`.

---

## Status machine

Allowed `status` values:

| Value | Meaning |
|-------|---------|
| `received` | Document is available in Drive (or otherwise obtained) at the expected granularity. |
| `not_received` | Still needed or unknown. |
| `n/a` | Confirmed not applicable for this taxpayer/year. |

### Transitions

| From | To | When |
|------|-----|------|
| `not_received` | `received` | User (or workflow) has uploaded/placed the document in Drive and the registry is updated. |
| `not_received` | `n/a` | User confirms the document does not apply. |

**Rules:**

- Only **CHITRA** (the assistant workflow) *proposes* status changes; the **user confirms** before treating a transition as authoritative.
- Status tracks **user upload and collection effort**, not whether a CPA has reviewed or accepted the document.

---

## Correspondence tracking

Used especially for **K-1 (Partnership)** and other documents that depend on **external sponsors** (email, portals, postal mail).

### Item shape

Each element of `correspondence`:

| Field | Type | Description |
|-------|------|-------------|
| `date` | `string` | ISO date of the message or event. |
| `from` | `string` | Sender or channel label (e.g. `Sponsor contact`, `Portal`). |
| `summary` | `string` | Short description of what was said or requested. |

### Link to follow-up

`nextFollowUp` should hold the **next** expected action date (reminder, expected K-1 arrival, etc.), logically **linked** to the latest `correspondence` thread—not a duplicate of `correspondence[].date`.

---

## Known gaps and limitations

| Gap | Impact | Mitigation |
|-----|--------|------------|
| No `receivedDate` field | Cannot sort or audit by actual receipt date in-schema. | Use `lastUpdated`, `notes`, or external logs; consider a future optional field. |
| No state-machine audit trail | Prior status values are not retained in JSON. | Manual git history or append-only changelog if needed. |
| `emptyFolders` goes stale | Lists folders that were empty at scan time; becomes wrong after uploads. | Re-run inventory; do not rely on `emptyFolders` as live truth. |
| Path scheme inconsistency | Mix of labeled vs numbered folder paths breaks sorting and scripts. | **Normalize** to a **numbered folder** scheme in `drivePath` and keys where possible. |
| ID gaps from deletions | Numeric `id`s are **stable** and **not sequential**. | Never assume `id` equals row index; do not renumber to fill gaps. |

---

## Example (fully anonymized)

```json
{
  "taxYear": 2025,
  "basedOn": "Prior year return snapshot + Drive inventory 2026-03-01",
  "lastUpdated": "2026-03-27",
  "driveInventoryFile": "extracted/drive-inventory-sample.json",
  "driveFolderStructure": {
    "Taxes/2025": "folder_id_placeholder_01",
    "01 - W-2s": "folder_id_placeholder_02"
  },
  "documents": [
    {
      "id": 1,
      "docType": "W-2",
      "issuer": "Employer Corp",
      "for": "Taxpayer A",
      "category": "Income",
      "availabilityMonth": "Jan",
      "status": "received",
      "driveFileId": "drive_file_id_placeholder",
      "driveFileName": "2025 W-2 Employer Corp Taxpayer A.pdf",
      "drivePath": "Taxes/2025/01 - W-2s/Taxpayer A/",
      "extractedData": {
        "wages": 0,
        "federalWithholding": 0,
        "ssWages": 0,
        "ssTax": 0,
        "medicareWages": 0,
        "medicareTax": 0,
        "box12Codes": {},
        "state": "TX"
      },
      "notes": "Example entry only; replace zeros with extracted values in real use.",
      "questions": [
        "Does Box 12 need reconciliation with brokerage supplemental?"
      ]
    },
    {
      "id": 5,
      "docType": "K-1 (Partnership)",
      "issuer": "Example LP",
      "for": "Joint",
      "category": "Income",
      "availabilityMonth": "Mar",
      "status": "not_received",
      "drivePath": "Taxes/2025/03 - Partnerships/Example LP/",
      "contact": {
        "name": "Contact Name",
        "email": "k1-notices@example.com",
        "company": "Example LP",
        "portal": "https://example.com/investor"
      },
      "correspondence": [
        {
          "date": "2026-02-15",
          "from": "Sponsor IR",
          "summary": "Requested ETA for final K-1; reply said late March."
        }
      ],
      "nextFollowUp": "2026-03-30",
      "questions": []
    }
  ],
  "emptyFolders": [
    "Taxes/2025/09 - Estimated Payments/"
  ],
  "majorDiscoveries": [
    "New rental property added mid-year; bank export pattern documented in notes."
  ],
  "cpaQuestions": {
    "overall": [
      "Confirm treatment of ISO disqualifying disposition vs AMT adjustment."
    ],
    "bySection": {
      "equity": [
        "Supplemental equity tax reporting aligns with `$XXX,XXX` ordinary income on W-2?"
      ]
    }
  }
}
```

---

## Versioning

This schema is described in Markdown for human review. Breaking changes to field names or `docType` strings should be noted in commit messages and, if the project ships a machine-readable schema, bumped there in lockstep.
