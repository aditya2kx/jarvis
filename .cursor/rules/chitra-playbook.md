---
description: CHITRA operational playbook -- year-start bootstrap, document workflows, CPA communication
globs: []
alwaysApply: false
---

# CHITRA Operational Playbook

This playbook describes how CHITRA bootstraps a new tax year, collects and files documents, runs specialized workflows (ISO dispositions, rental Schedule E, business Schedule C), communicates with a CPA, manages mid-season changes, and recovers from common tooling errors. **Do not embed personal data here or in derived artifacts**—use placeholders such as `{Employer}`, `{Broker}`, `{Property Address}`, `{Account}`, and generic examples only.

---

## Section 1: Year-Start Bootstrap

### 1.1 Drive folder structure

- Run **`organize_drive_folders.py`** or **`bootstrap.py`** (per project convention) to create the annual Drive tree under the configured taxes root (e.g., `Taxes/{taxYear}/` with subfolders for wages, brokerage, rental, business, charitable, ISO exports, CPA handoff, etc.).
- Verify folder IDs align with `config.yaml` (`google_drive.taxes_year_id` and any per-workstream folder IDs if used).
- Confirm sharing and permissions match prior-year practice (view vs edit for collaborators).

### 1.2 Google Sheet: Tax Tracker

Create or duplicate the prior-year **CHITRA Tax Tracker** spreadsheet and set tabs:

| Tab | Purpose |
|-----|---------|
| **Document Checklist** | One row per expected document; status Received / Not Received; links to Drive |
| **Return Summary** | High-level numbers pulled from filed return and estimates (no sensitive literals in playbook examples) |
| **Changes Log** | Life events and return-impacting changes for the year |
| **CPA Questions** | Open items for the preparer; short, consolidated questions |
| **CPA Document Navigator** | Folder-centric view for CPA: paths, purpose, file lists, status |

Wire `google_sheets.tax_tracker_id` in `config.yaml` to the new sheet ID.

### 1.3 Prior-Year Return Analysis (PRIMARY INPUT)

The prior-year federal and state tax returns are the **single most important input** for bootstrapping a new tax year. They tell CHITRA what existed last year, which drives what to expect this year.

**Step 1 — Parse the prior-year return:**
- Extract every schedule, form, and line item from the filed federal and state returns.
- For each: identify the issuer, document type, portal/source, and whether it's likely to recur.
- Map each line item to a document category (W-2, 1099, K-1, 1098, property tax, etc.).

**Step 2 — Derive the document checklist:**
- Every recurring issuer/document from last year becomes a `not_received` entry in the new registry.
- Preserve issuer names, EINs, portal credentials references, and folder conventions.
- Reset `extractedData` and `status` for all entries.
- Bump `taxYear` everywhere.

**Step 3 — Derive the folder structure (NEVER copy from benchmark):**
- Build the Drive folder tree from the derived checklist categories and document fields, not from a manual template or existing Drive tree.
- Use `drive-folder-convention.md` for naming rules.
- **Category folders** (top-level) come from `CATEGORY_FOLDERS` map in `derive_registry_from_return.py`, keyed by `docType`.
- **Subfolders** come from document fields: `issuer` (normalized via `ISSUER_BRAND_MAP`), `for` (person name), `address` (property tag), `businessName`.
- Naming patterns: `{category}/{person} - {employer}` for W-2s, `{category}/{broker_brand}` for 1099s, `{category}/{city} Rental - {address}` for properties, `{category} - {short_address}` for primary residence.
- `ISSUER_BRAND_MAP` normalizes legal entity names to common brands (`Charles Schwab & Co., Inc` → `Schwab`, `E*Trade from Morgan Stanley` → `E-Trade`). This map is extensible config, not per-user data.
- When a derived folder name doesn't match the benchmark during validation: **update the naming rules or brand map** in code — never peek at the real `Taxes/2025` folder to see the "right answer".
- Every document in the registry carries a `drivePath` field set during derivation. Upload uses this path, not a runtime lookup against existing Drive folders.

**Step 4 — Check yourself first, ask second:**

The cardinal rule: **never ask the user what you can check yourself.** Triage every gap:

| Triage | Action | Examples |
|--------|--------|----------|
| **Self-check** | Use Playwright to check portals, download docs | Broker 1099s, bank 1098s, county property tax, HSA forms |
| **Bank-derived** | Analyze bank transactions to identify recurring payees | Insurance provider (monthly premium), property manager (monthly deposit) |
| **Address-derived** | From a street address, derive county, state, tax portal URLs | "{City} {State}" → {County} → county CAD/tax sites |
| **Must-ask** | Only the user can tell you | Life events, new arrangements, employer changes, CPA identity |
| **User-provides** | Portal auth is too complex to automate | Employer W-2s, 1095-Cs from HR portals with heavy SSO/MFA |

**Step 5 — Ask smart questions (learnings from live testing):**

Principles for the questionnaire conversation:
1. **Batch by category** — group related questions, don't send a numbered list of 35
2. **Start with highest-impact** — life events and business changes discover the most new docs
3. **Follow up intelligently** — when user says "bought a house at [address]":
   - Auto-derive: address → county → property tax portal URL → homestead eligibility
   - Ask only what you can't derive: "Who's the mortgage lender?" (to know where the 1098 comes from)
4. **Ask about employees for Schedule C businesses** — triggers 4 employer tax docs (W-2, W-3, 941, 940) plus "which payroll service?" to know the portal
5. **Ask about health insurance dependency** — who's the primary policyholder can change year to year
6. **Match the user's tone** — casual user gets casual CHITRA. Use first names not "taxpayer" and "spouse". If user uses nicknames, mirror that warmth.
7. **Explain WHY you're asking** — non-tax-professionals need context: "The property manager issues a 1099-MISC for your rental income. We need to know who to expect it from."
8. **Gmail is a document source** — CPA correspondence, charitable arrangements, and K-1 notifications often arrive by email. Build Gmail skill as priority.

**Step 6 — Pull documents autonomously:**
- For every portal where credentials are stored (Keychain + `portals.yaml`), attempt automated retrieval via Playwright.
- **MFA/OTP rule (CRITICAL):** When a portal requires a verification code, **ALWAYS notify the user via Slack DM** using `skills.slack.adapter.request_otp()`. NEVER rely on the user seeing a message in the IDE — they may not be at their computer. The Slack DM is the primary communication channel for time-sensitive requests.
  - If the portal offers email-based OTP, prefer that option — once Gmail skill is built, Chitra can read OTP emails autonomously.
  - If only SMS/phone is available, send the Slack DM explaining which portal and what phone number received the code, then poll for the user's reply.
  - User Slack ID and DM channel are in `config.yaml` under `slack.primary_user_id` / `slack.dm_channel`.
- For documents that can't be pulled (CPA-provided, user-uploaded), mark as `not_received` and notify via Slack.
- Come back to the user with a **status report**, not more questions: "Downloaded brokerage 1099s from 3 portals. Partnership K-1 isn't on the portal yet — want me to email the CPA?"

This replaces the old approach of manually maintaining the registry. The return is the source of truth; the registry is derived from it.

### 1.4 `estimates.json` initialization

- Seed **`estimates.json`** with a **baseline scenario** derived from the **filed prior-year return** (structure only: brackets, filing status assumptions, major line buckets as your schema defines).
- Do not treat prior-year numbers as final for the new year; they anchor scenario math until current-year documents replace them.

### 1.5 `changes.json` initialization

- Create **`{taxYear}/changes.json`** with an **empty `changes` array** (or your schema’s equivalent) unless carryover narrative items are explicitly required on day one.

### 1.6 `access-tracker.json` portal maintenance

- **Update portal URLs** for each institution: many brokerages, payroll providers, and property portals **change login URLs or SSO flows annually**.
- Refresh **notes** for multi-factor, app-specific passwords, and “where to click” hints without storing secrets in plain text in shared docs—prefer password manager references and generic step labels.
- Mark **unknown** access rows for verification in Q1 before document season peaks.

### 1.7 Script order (recommended)

1. Bootstrap Drive folders.  
2. Create / clone Sheet + tabs.  
3. Initialize JSON files (registry, estimates, changes, access tracker).  
4. Run **`populate_sheet.py`** (or equivalent) once registry is valid JSON.  
5. Spot-check Sheet vs Drive vs registry.

---

## Section 2: Document Collection Playbook

For each document type below: **When** it usually arrives, **Where** to get it, **How to name** the file, **Where to file** in Drive, **What to extract** into `extractedData`, and **Gotchas** from practice.

**General naming pattern (adapt to house style):**

```text
{YYYY}-{DocType}-{IssuerShort}-{OptionalDetail}.pdf
```

Example (generic): `2025-W2-{Employer}-wage.pdf` — use consistent casing and hyphens so scripts can parse.

**General `extractedData` discipline:**

- Store **machine-useful** fields the CPA and scripts need (boxes, payer EIN if policy allows, property ID tags).
- Avoid duplicating entire PDF text unless your pipeline requires it; prefer structured keys aligned with `document-registry.json` schema.

---

### 2.1 W-2 (wages)

| Dimension | Guidance |
|-----------|----------|
| **When** | Typically January–February; corrections (W-2c) can arrive later. |
| **Where** | Employer payroll portal, or postal mail from `{Employer}` HR/payroll. |
| **How to name** | `YYYY-W2-{Employer}.pdf` (add state suffix if multiple state rows). |
| **Where to file** | Drive: wages / `{Employer}` / year folder per your tree. |
| **What to extract** | Boxes 1–6, 12 codes with **amounts**, state IDs, local wages if applicable. |
| **Gotchas** | **RSU / equity** often appears in **Box 12** with specific codes—capture code + amount pairs, not just Box 1. **State withholding** varies by work vs residence allocation; multiple states ⇒ multiple W-2 rows or attachments. **Multiple employers** ⇒ one registry row per W-2; do not merge. |

---

### 2.2 Brokerage 1099 (consolidated + supplements)

| Dimension | Guidance |
|-----------|----------|
| **When** | Mid-February typical; amended 1099s (Corrected) can arrive March–April or later. |
| **Where** | `{Broker}` document center; sometimes email notification with download link. |
| **How to name** | `YYYY-1099-{Broker}-{AccountLast4}.pdf`; add `-CORRECTED` if superseding. |
| **Where to file** | Drive: brokerage / `{Broker}` / per-account folders. |
| **What to extract** | Dividends, interest, proceeds basis totals, federal/state withholding, **RSU / option** sections if broken out. |
| **Gotchas** | **Multiple accounts** ⇒ multiple PDFs—each gets a checklist row. **TOD / beneficiary accounts** may show **no activity** but still need confirmation of zero. **Stock plan supplements** may be **separate PDFs** from the consolidated 1099—track both. **ISO vs RSU** coverage: verify supplemental equity statements match vesting platforms; **do not assume** one PDF has everything. |

---

### 2.3 K-1s (partnerships, S-corps, trusts, LLCs)

| Dimension | Guidance |
|-----------|----------|
| **When** | March is common; **many arrive April–August** (extensions at entity level). |
| **Where** | Postal mail, secure email from entity, or **third-party portals** (e.g., some property or fund managers use vendor portals—placeholder: “Yardi-like” systems). |
| **How to name** | `YYYY-K1-{EntityName}-{EIN-or-short-id}.pdf` |
| **Where to file** | Drive: pass-through / `{Entity}` / year. |
| **What to extract** | Ordinary income, interest, dividends, capital gains, Section 179, foreign items, state apportionment footnotes as your schema supports. |
| **Gotchas** | **Late K-1s** are normal—track **expected** vs **received** aggressively. **Correspondence** (emails asking for tax IDs or confirmations) must be logged so nothing is filed without the final K-1. Some K-1s **only** post to a portal—update `access-tracker.json` when URLs change. |

---

### 2.4 Form 1098 (mortgage interest)

| Dimension | Guidance |
|-----------|----------|
| **When** | January–February. |
| **Where** | Lender portal or mail from `{Servicer}`. |
| **How to name** | `YYYY-1098-{PropertyTag}-{Servicer}.pdf` |
| **Where to file** | Drive: property folder for that address tag (rental vs primary—separate subfolders). |
| **What to extract** | Box 1 mortgage interest, Box 2 outstanding principal if used, escrow/tax boxes if present, property address on form. |
| **Gotchas** | **Same form type** for **rental vs primary**, but **tax treatment differs**: rental interest flows to **Schedule E**; primary residence may interact with **Schedule A** subject to limits. **Real estate taxes** shown on 1098 may be **partial** (escrow)—reconcile to actual tax bills. |

---

### 2.5 Property tax

| Dimension | Guidance |
|-----------|----------|
| **When** | Installment schedules vary (e.g., semi-annual); **year-end** bills may straddle filing. |
| **Where** | County assessor / collector website, mail, or escrow annual summary from lender. |
| **How to name** | `YYYY-PropertyTax-{County}-{PropertyTag}-installment{N}.pdf` |
| **Where to file** | Drive: property taxes / `{PropertyTag}` / year. |
| **What to extract** | Amount paid, assessment ID, calendar date paid, penalty lines if any. |
| **Gotchas** | **Late penalties** may affect **deductibility**—CPA decides; capture penalty lines separately. **Rental vs primary** allocation matters. **Installment timing**: pay date must fall in the tax year for cash-basis reporting unless CPA instructs otherwise. |

---

### 2.6 Rental insurance

| Dimension | Guidance |
|-----------|----------|
| **When** | Policy documents on renewal; declarations page anytime. |
| **Where** | Carrier portal, agent email, or mail. |
| **How to name** | `YYYY-Insurance-{Carrier}-{PropertyTag}-policy{ID}.pdf` |
| **Where to file** | Drive: rental / `{PropertyTag}` / insurance. |
| **What to extract** | Named insured, **policy period** start/end, **total premium**, covered address. |
| **Gotchas** | Policies often **span calendar years**—for an annual deduction, **prorate premium** to the tax year (see Section 4). Do not book 12 months of a mid-year renewal entirely in one year without proration logic. |

---

### 2.7 1099-MISC / 1099-NEC (rental income)

| Dimension | Guidance |
|-----------|----------|
| **When** | January–February; corrections possible. |
| **Where** | Property management company portal or mail. |
| **How to name** | `YYYY-1099-{FormType}-{MgmtCo}-{PropertyTag}.pdf` |
| **Where to file** | Drive: rental / `{PropertyTag}` / income. |
| **What to extract** | Rents in Box 1 (or NEC equivalent per form revision), payer TIN if policy allows. |
| **Gotchas** | **Management company gross rents** may **differ from bank deposits** (fees, holdbacks, timing)—**reconcile** to bank statements before filing; note timing and reserve items in registry notes. |

---

### 2.8 Charitable instruments (non-cash, trusts, DAFs, complex gifts)

| Dimension | Guidance |
|-----------|----------|
| **When** | Depends on gift timing; acknowledgments often follow within weeks. |
| **Where** | Charity portal, donor-advised fund sponsor, or legal counsel for complex structures. |
| **How to name** | `YYYY-Charitable-{RecipientShort}-{InstrumentType}.pdf` |
| **Where to file** | Drive: charitable / year / subfolder by structure type. |
| **What to extract** | Date of gift, asset description class (high level), acknowledgment letter presence—not valuation conclusions. |
| **Gotchas** | **Complex structures are not simple donations.** Split-interest trusts, bargain sales, and nonmarketable assets require **CPA and often appraisal** rules. CHITRA **records facts**; **treatment** is CPA judgment. |

---

### 2.9 ISO dispositions (equity comp)

| Dimension | Guidance |
|-----------|----------|
| **When** | Broker confirms post-sale; some forms (e.g., **3921**) follow separate schedules. |
| **Where** | Equity plan provider + `{Broker}` trade confirms. |
| **How to name** | Follow ISO tracker convention (Section 3); keep sale confirms with brokerage year folder. |
| **Where to file** | Drive: ISO / `{Broker}` / exports + PDF confirms. |
| **What to extract** | Exercise date, sale date, shares, proceeds, basis shown on 1099-B, grant IDs. |
| **Gotchas** | **Form 3921** availability and use is a **CPA decision**. **AMT basis** is **not** the same as **strike price** in many cases—**FMV at exercise** drives AMT. **Lot-level** tracking is essential; **partial exercises** split across rows (Section 3). |

---

## Section 3: ISO Disposition Workflow

### 3.1 Source of truth

- **Primary:** Google Sheet tab on the **ISO disposition tracker** (ID from `config.yaml`: `google_sheets.iso_tracker_id`).
- **Mirror:** `knowledge-base/iso-tracker.json` for scripting, diffing, and offline analysis.

### 3.2 Row convention

- **One row per disposition** (or per partial lot if that is your sheet design—stay consistent all year).
- **Column convention (example):** **Col H = Col K** where those columns represent matched basis or share counts per your template—**do not break** this invariant without updating scripts and docs.
- **Partial exercise splits:** multiple rows tied to the same grant/exercise event must cross-reference (grant ID + exercise date) so totals reconcile to broker statements.

### 3.3 Governance

- **NEVER** update the ISO Sheet without **explicit user approval** (same rule as in core CHITRA rules). Propose deltas; apply after confirmation.

### 3.4 Reconciliation

- Match **broker “Opened” / order date** to **plan exercise transaction date** within allowed slippage (T+1, timezone, batch posts).
- When numbers disagree, **flag** rather than silently “fixing”—prefer a reconciliation note row or comment field.

### 3.5 AMT and credit

- Track **AMT credit carryforward** pattern per CPA model (often coordinated with Form 6251 history).
- **Basis warning:** **1099-B** may show **strike** as basis while **AMT** uses **FMV at exercise**—do not conflate; store both when known.

### 3.6 Export

- **CSV** to the broker-related folder in Drive for CPA handoff.
- **JSON snapshot** with **dated filename** (e.g., `iso-tracker-snapshot-YYYY-MM-DD.json`) before major edits or CPA sends.

---

## Section 4: Rental Property Workflow (Schedule E)

### 4.1 Document set

Collect for **each** `{Property Address}` (use tags, not literal addresses in shared rules):

- **1098** (mortgage interest) if applicable.
- **1099-MISC/NEC** for rents from management.
- **Property tax** bills (all installments in the tax year).
- **Insurance** declarations for policies **in force** during the year—if renewal falls mid-year, retain **both** policy periods that touch the year.
- **Bank transaction CSV** (property-dedicated account strongly preferred).

### 4.2 Expense categories (illustrative)

- Mortgage interest (reconcile to 1098).
- HOA / regime fees.
- Property management fee.
- Property tax (cash paid).
- Insurance (**prorated**—see below).
- Repairs vs improvements (CPA classification—track spend with vendor receipts).

### 4.3 Insurance proration formula

For each policy with premium `P` and policy period covering the tax year:

```text
Deduction portion ≈ (months overlapping tax year / 12) × annual premium P
```

Refine to **days** if your CPA requires precision for mid-year start/stop. Apply **per policy**; sum for the property.

### 4.4 Bank transaction parsing

- Export **CSV** from the bank; identify **recurring** payees by normalized description pattern (HOA, utility names, management company).
- Tag transactions with **property ID** in your registry or ledger—not free text in multiple incompatible places.

### 4.5 Schedule E vs Schedule A

- **Rental** property deductions attributable to the rental go on **Schedule E** (subject to passive activity and other rules—CPA).
- **Primary residence** mortgage interest and taxes (if eligible) belong on **Schedule A** paths—not commingled with rental rows.

---

## Section 5: Business (Schedule C) Workflow

### 5.1 Portal access model

- The **CPA** may receive **direct read-only portal access** to `{Bank}`, `{Payroll Provider}`, `{State Comptroller}`—coordinate invites and expiry dates in `access-tracker.json` notes (no passwords in plaintext).

### 5.2 Supplemental categorization

- You provide **categorized transactions** with **location / entity splits** when one legal entity or one bank account serves multiple sites or businesses.

### 5.3 Startup costs (IRC 195)

- **Expenses before business opens** may be **capitalized and amortized** rather than expensed immediately—**CPA determines** eligibility and election. CHITRA tracks dates and amounts as separate tags.

### 5.4 Multi-location, one bank account

- Single operating account with **user-defined splits** (percent or fixed allocation) per month or per transaction class—document methodology in `changes.json` when the policy changes.

### 5.5 Personal account transactions

- If any business spend flows through a **personal** card or account, **flag clearly** in the registry and in CPA navigator notes so nothing is double-counted or missed.

---

## Section 6: CPA Communication Playbook

### 6.1 Initial email timing

- Send the **first comprehensive package** after **all documents that are reasonably available** are collected—typically **before April 15**, unless strategy dictates extension-first (late K-1s).

### 6.2 Email structure

1. **Filing overview** — entities, forms expected (1040 + schedules), extension status if any.  
2. **Attachment guide** — point to **navigator CSV** + **zip** of organized folders (or Drive share link per practice).  
3. **High-level questions only** — consolidated in **CPA Questions** tab; avoid duplicating per-document noise.  
4. **Action items** — numbered list for CPA and for taxpayer with owners.

### 6.3 Navigator CSV semantics

- **One row per leaf folder** (not per file): columns should include **folder path**, **purpose**, **file list**, **status**.
- Update navigator when folder contents change—CPA relies on it as a map.

### 6.4 Extension strategy

- If **K-1s or other critical items** are **late**, file **extension** and pay **estimated tax** due with extension per CPA guidance—do not “wait and see” without a payment plan.

### 6.5 Follow-up handling

- When the CPA replies with questions:  
  1. Update **`document-registry.json`** (status, notes).  
  2. Refresh **CPA Document Navigator** export.  
  3. Re-export CSV / zip snapshot.  
  4. Confirm **Sheet** tabs match JSON after `populate_sheet.py`.

---

## Section 7: Change Management

### 7.1 Pipeline

1. **Classify** the change (document, life event, estimate parameter, new entity).  
2. **Clarify** with the user if intent or amounts are ambiguous.  
3. Update **`changes.json`** (narrative + structured fields).  
4. Update **`estimates.json`** scenarios affected.  
5. Update **`document-registry.json`** (new rows, status, notes).  
6. Run **`populate_sheet.py`** (or equivalent) to sync Sheet.  
7. **Confirm** Sheet, JSON, and Drive agree.

### 7.2 Life events that trigger changes

- New property acquisition or disposition.  
- Change of **domicile** or multi-state work pattern.  
- Job or **employer** change.  
- New **business location** or entity formation.  
- New **investment** types (options, crypto, partnerships).

### 7.3 Mid-season corrections

- **Document removed** from scope: prefer explicit **status** + note; if IDs are **deleted**, be aware **gaps** in numeric IDs may confuse naive scripts—follow project convention for tombstones vs deletion.  
- **Status changes** propagate to both JSON and Sheet.  
- **New CPA questions**: add to registry + **CPA Questions** tab; avoid duplicate channels (email vs Sheet) without cross-links.

---

## Section 8: Error Catalog

### 8.1 StrReplace / Unicode in markdown or JSON

- **Symptom:** Bulk find-replace fails or mismatches on arrow characters.  
- **Cause:** Visually similar glyphs—e.g., Unicode arrow `→` vs ASCII `->` vs escaped `\u2192`.  
- **Fix:** Use a **small Python script** with explicit Unicode escapes for complex markdown or JSON edits; avoid relying on editor replace for non-ASCII.

### 8.2 Google Sheets API HTTP 400

- **Symptom:** Batch update returns 400.  
- **Mitigation:** **Retry once** (transient validation or quota quirks).  
- **Check:** Payload for **malformed ranges**, wrong sheet name, or **grid limits** exceeded.

### 8.3 `gsheets_read` partial ranges

- **Symptom:** Data loads without header row.  
- **Cause:** Range starts below row 1 or omits header columns.  
- **Fix:** Prefer reading **`A1:Z`** (or full width) on tabular sheets so **row 1 headers** always load.

### 8.4 `populate_sheet.py` — `JSONDecodeError`

- **Symptom:** Script crashes parsing registry or estimates.  
- **Cause:** **Trailing commas** in JSON objects/arrays.  
- **Fix:** Run JSON through `python -m json.tool` or linter; remove trailing commas; re-run.

### 8.5 Drive upload HTTP 400

- **Symptom:** File upload fails.  
- **Mitigation:** Retry once; verify **folder ID** matches the intended year folder; check **file size** and MIME type limits.

### 8.6 OAuth token refresh

- **Symptom:** Scripts lose auth after long idle or credential rotation.  
- **Note:** Many scripts refresh tokens **in memory only**—process exit loses session.  
- If **refresh token is revoked**, local scripts cannot recover without **re-authentication**; use **MCP** or the project’s OAuth flow to re-establish credentials, then update stored tokens per security practice.

---

## Appendix: Placeholder quick reference

| Placeholder | Use for |
|-------------|---------|
| `{Employer}` | W-2 issuer |
| `{Broker}` | Brokerage / custodian |
| `{Property Address}` | Property tag in filenames—prefer opaque tags in shared docs |
| `{Account}` | Last-4 or internal account label |
| `{Entity}` | Partnership / LLC / trust name on K-1 |
| `{Servicer}` | Mortgage servicer |
| `{MgmtCo}` | Property manager |

---

*End of playbook. Keep operational examples generic; store taxpayer-specific values only in private config and registries inside the workspace.*
