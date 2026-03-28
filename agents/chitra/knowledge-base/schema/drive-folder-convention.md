# Google Drive folder naming convention

This document describes a repeatable layout for tax-year document storage in Google Drive. All examples use **generic placeholders**—replace them with your own labels; do not commit real PII or account identifiers to public repositories.

## Root layout

- **Root path**: `Taxes/{YYYY}/`
- `{YYYY}` is the tax year (calendar year for most individual filers), e.g. `2025`.

Everything for that year lives under this single root so searches and shared links stay scoped to one filing season.

## Numbered top-level folders

Use **two-digit numeric prefixes** `01` through `10` so folders sort in a fixed order regardless of Drive’s default sort.

| Prefix | Purpose (high level) |
|--------|----------------------|
| `01` | W-2s and employment |
| `02` | Brokerage 1099s |
| `03` | Partnerships and rental properties |
| `04` | Primary residence |
| `05` | Charitable |
| `06` | Retirement accounts |
| `07` | HSA and health insurance |
| `08` | Business |
| `09` | Tax payments and extensions |
| `10` | Carryovers and prior year |

## Folder naming pattern

**Pattern**: `NN - Category Name`

- `NN` — zero-padded two digits matching the slot above (`01`–`10`).
- `Category Name` — short, human-readable label (title case is fine; stay consistent within the year).

**Optional suffixes** (square brackets, appended to the folder name when status matters):

| Suffix | Meaning |
|--------|---------|
| `[NEED DOCS]` | Expected documents not yet received or filed here. |
| `[NEED K-1]` | Waiting on Schedule K-1 (partnership, S-corp, trust, etc.). |
| `[NEED W-2s]` | Employment forms still outstanding. |
| `[NEED FROM CPA]` | Waiting on CPA deliverable (letter, organizer, review notes). |
| `[NEW INVESTMENT - NEED DOCS]` | New account or position; confirm cost basis and year-end statements. |

Use **at most one primary status suffix** per folder when possible; if multiple issues apply, prefer the most blocking label or split into a subfolder (see below).

## Subfolders

**Use subfolders when**:

- Multiple distinct **entities** or **issuers** exist under one numbered category (e.g. several employers, several brokers).
- You need **location- or property-specific** separation (e.g. multiple rentals).
- A single folder would mix **different document types** that you retrieve independently (e.g. one broker’s 1099-DIV vs 1099-B).

**Naming**: Prefer **entity/issuer-specific** names without embedding sensitive numbers:

- Employer or payroll provider name (short form).
- Broker or custodian name.
- Property or entity label (city + short descriptor, or entity legal name if appropriate for your threat model).

Avoid putting SSNs, full account numbers, or exact dollar amounts in folder names.

## File naming

**Pattern**: `{YYYY} {Form Type} - {Issuer} - {Key Detail}.{ext}`

| Part | Description |
|------|-------------|
| `{YYYY}` | Tax year the document supports. |
| `{Form Type}` | e.g. `W-2`, `1099-DIV`, `1099-B`, `K-1`, `1098`, `5498`. |
| `{Issuer}` | Employer, broker, lender, partnership, etc. |
| `{Key Detail}` | Short disambiguator: property nickname, account purpose, “final vs corrected”, “estimated vs final”. |
| `{ext}` | `pdf`, `png`, etc. |

**Amounts in filenames** (optional but helpful):

- Use when you have **multiple drafts** or **corrected** documents and the amount helps you pick the right file without opening it.
- Keep labels **rounded or abbreviated** if you use them in shared drives (e.g. `est-federal-withholding` instead of exact withholding).
- For open-source documentation, use **placeholder** amounts only in examples.

### Example file names (generic)

- `2025 W-2 - Example Corp - primary role.pdf`
- `2025 1099-DIV - Sample Brokerage - taxable account.pdf`
- `2025 1099-B - Sample Brokerage - summary.pdf`
- `2025 K-1 - Demo LP - Project Alpha.pdf`
- `2025 1098 - Example Bank - primary mortgage.pdf`
- `2025 5498 - Demo IRA Provider - traditional IRA.pdf`

## Specific folder inventory template

Use this as a **starting tree**; adjust labels to match your situation.

```
01 - W-2s & Employment/
  {Taxpayer Name} - {Employer}/
02 - Brokerage 1099s/
  {Broker Name}/
03 - Partnerships & Rental Properties/
  {Location} - {Entity Name} [{NEED K-1}]/
04 - Primary Residence - {Address}
05 - Charitable
06 - Retirement Accounts/
  {Provider}/
07 - HSA & Health Insurance
08 - Business - {Business Name}
09 - Tax Payments & Extensions
10 - Carryovers & Prior Year
```

**Notes on the template**

- **`{Taxpayer Name}`** — Use initials or a household label if you prefer minimal PII in folder names.
- **`{Address}`** — A short slug (e.g. city + “main home”) is enough for many workflows; full street address is optional and increases exposure if the tree is shared.
- **Bracket suffix on line 03** — Example shows `[NEED K-1]` on a property/entity folder; remove or replace when the K-1 is filed.

## Suffix usage rules (summary)

1. **Add a suffix** when the folder is tracking **missing or incomplete** work—not for every folder every year.
2. **Remove the suffix** when the condition is cleared (documents received, K-1 uploaded, CPA item delivered).
3. Prefer **clear subfolders** over long compound suffixes when you have two unrelated blockers (e.g. one property waiting on K-1 and another complete).

## When to create subfolders (quick checklist)

| Situation | Recommendation |
|-----------|----------------|
| One employer, one W-2 | Single PDF in `01 - …` may be enough; subfolder optional. |
| Two employers or joint filers with separate docs | Per-taxpayer or per-employer subfolders. |
| Multiple brokerage accounts at one custodian | Often one subfolder per custodian; use filenames to separate accounts. |
| Several rentals or partnerships | One subfolder per property or per entity. |
| Corrected 1099 after filing | Keep original and corrected with filenames that say `corrected` and date if needed. |

This convention is descriptive, not a substitute for professional tax advice. Adapt categories to your filing complexity.
