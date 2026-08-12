# Issue #248 — Ordered tubs (Actuals) table on Inventory / Ordering

Evidence tier: sandbox-e2e

## Jam / §4 (approved in chat 2026-08-12)

Add a **read-only** table on Operator Console `/inventory` showing **uploaded restock actuals** (`inventory_restock_orders` only — never water-fill estimates). Layout: **rows = delivery dates** (newest first), **columns = `ACTIVE_BASES` + TOTAL**. Filter with the shared **Period** control (`resolvePageRange` / `oc_range` cookie, America/Chicago). Section-local Period (runway / reco / usage unchanged). Missing item on a date that has actuals → `0`. Dates with schedule/estimates only → omitted. Empty Period → muted copy, not DataTable “No rows.”

Feature flag: **none** — additive read UI; cannot silently produce wrong order numbers.

### Per-scenario evidence (PR §4)

| # | Scenario | Pass criterion |
|---|---|---|
| E1 | Default Period | `/inventory` shows **Ordered tubs (Actuals)**; dates × bases; 2026-08-12 total 55. Hosted screenshot `i248-actuals-default` |
| E2 | This month | `/inventory?range=this_month` includes Aug 3 / 12 / 20; excludes July. Screenshot `i248-actuals-this-month` |
| E3 | Last 7 days | `/inventory?range=7d` ends Chicago today; **no** 2026-08-20 if that date is after today. Screenshot `i248-actuals-7d` |
| E4 | Empty Period | Custom window with no actuals → “No uploaded Actuals in this Period.” Screenshot `i248-actuals-empty` |
| E5 | Legacy | Reco / runway / usage unchanged; reco can still show Estimated. Screenshot `i248-reco-unchanged` |
| E6 | Polish | Date frozen; bases scroll; Period `FilterSelect` + custom `DateRangePicker`; ~44px tap; 390px screenshot `i248-actuals-mobile` |
| E7 | Tests | Vitest pivot + SQL filter; `python3 scripts/verify.py --full` green |
| E8 | Post-merge | Prod `/inventory?range=this_month` vs BQ `inventory_restock_orders` spot-check |

Capture:

```bash
python3 apps/operator-console/scripts/capture_evidence.py \
  --path '/inventory' --label i248-actuals-default \
  --scroll-to '[data-testid=ordered-tubs-actuals]'
python3 apps/operator-console/scripts/capture_evidence.py \
  --path '/inventory?range=this_month' --label i248-actuals-this-month \
  --scroll-to '[data-testid=ordered-tubs-actuals]'
python3 apps/operator-console/scripts/capture_evidence.py \
  --path '/inventory?range=7d' --label i248-actuals-7d \
  --scroll-to '[data-testid=ordered-tubs-actuals]'
python3 apps/operator-console/scripts/capture_evidence.py \
  --path '/inventory?range=custom&from=2026-01-01&to=2026-01-07' --label i248-actuals-empty \
  --scroll-to '[data-testid=ordered-tubs-actuals]'
python3 apps/operator-console/scripts/capture_evidence.py \
  --path '/inventory' --label i248-reco-unchanged
python3 apps/operator-console/scripts/capture_evidence.py \
  --path '/inventory?range=this_month' --label i248-actuals-mobile \
  --width 390 --scroll-to '[data-testid=ordered-tubs-actuals]'
```

UX polish (`docs/contributing/ui-polish.md` lines 24–31): reuse `DataTable`, `FilterSelect`, `DateRangePicker`, `PageHeader` patterns, muted methodology note. Hover/focus-visible on Period select (existing `SelectTrigger`); empty state muted text; `min-h-10` cells via DataTable.

## Invariants preserved

- **Read-only** — no `writes.ts` / mutating actions changes; restock replace-per-date unchanged.
- Actuals-only: query `inventory_restock_orders`, never `inventory_order_reco` estimates.
- America/Chicago Period via `resolvePageRange` (`apps/operator-console/lib/filters/period.ts` lines 18–40).
- Integer tubs display (digits 0); `quantity_tubs` FLOAT64 stored as-is.
- Reco / runway / usage-day tables must not break.
- Idempotent upserts N/A (no writes).

## Docs lock-step

| Change | Doc |
|---|---|
| Inventory reads | `docs/operator-console/ARCHITECTURE.md` line 169 (Inventory / Ordering row) |
| Checker | `python3 scripts/check_doc_freshness.py --base origin/main` |
| Notable ship | `PROGRESS.md` via post-merge retro (not direct main) |
| CONTRIBUTING | PR §4 evidence contract (this plan) |

No RUNBOOK / DOMAIN — no pipeline or sheet change.

## Branch / PR mechanics

- Branch: `fix/i-want-to-work-on-a-4` (Issue #248).
- `gh pr create --base main` as `jarvis-agent-bot328`; never self-merge; babysit.
- Cost: `pr_cost_ledger.py bind-pr` + `sync` after PR exists. One branch = one PR.

Model routing: Sonnet for all milestones. One chat per PR.

---

## Milestone 1 — Query + pivot (Sonnet)

### Files

| Path | Change |
|---|---|
| `apps/operator-console/lib/bq/queries.ts` after `scheduledRestockDates` (~line 1857) | Add `RestockActualsRow` + `restockActuals(store, win)` |
| `apps/operator-console/lib/inventory/restockActuals.ts` (new) | `pivotRestockActuals(rows)` → date × base matrix rows |
| `apps/operator-console/__tests__/restock-actuals.test.ts` (new) | Pivot + SQL assertions via mocked `q` |

### Signatures

```typescript
export interface RestockActualsRow {
  delivery_date: string;
  item: string;
  quantity_tubs: number | null;
}

export function restockActuals(
  store: string,
  win: DateWindow,
): Promise<RestockActualsRow[]>

export type RestockActualsPivotedRow = {
  date: string;
  TOTAL: number;
  [base: string]: string | number;
};

export function pivotRestockActuals(
  rows: RestockActualsRow[],
  bases?: readonly string[],
): RestockActualsPivotedRow[]
```

SQL (parameterized; `dateParam` for DATE binds — `apps/operator-console/lib/bq/client.ts` lines 65–71):

```sql
SELECT CAST(delivery_date AS STRING) AS delivery_date, item, quantity_tubs
FROM `…inventory_restock_orders`
WHERE store = @store
  AND delivery_date BETWEEN @start AND @end
ORDER BY delivery_date DESC, item
```

**Verify:** `cd apps/operator-console && npx vitest run __tests__/restock-actuals.test.ts`

---

## Milestone 2 — Inventory page Period + table (Sonnet)

### Files

| Path | Change |
|---|---|
| `apps/operator-console/app/inventory/page.tsx` lines 52–212 | `searchParams`; `resolvePageRange`; fetch `restockActuals`; section after `OrderRecoTable` |
| `apps/operator-console/components/inventory/OrderedTubsActualsTable.tsx` (new, optional thin wrapper) | `data-testid=ordered-tubs-actuals`; empty copy; `DataTable` pinLeft `date` |

Reuse: `FilterSelect` (`components/filters/FilterSelect.tsx` lines 26–72), `DateRangePicker`, `RANGE_PRESETS`, `wantsCustom`, `DataTable` `meta.format` `{ kind: "number", digits: 0 }`.

Page `searchParams` shape (mirror labor `app/labor/page.tsx` lines 87–103, narrower):

```typescript
export default async function InventoryPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string; from?: string; to?: string }>;
})
```

**Verify:** `cd apps/operator-console && npx vitest run __tests__/restock-actuals.test.ts __tests__/order-reco-pivot.test.ts __tests__/usage-day-audit.test.ts`

---

## Milestone 3 — Docs + verify (Sonnet)

### Files

| Path | Change |
|---|---|
| `docs/operator-console/ARCHITECTURE.md` line 169 | Inventory reads: Period-filtered `inventory_restock_orders` Actuals table |

**Verify:**

```bash
python3 scripts/check_plan_readiness.py --plan docs/plans/i248-inventory-ordered-tubs-actuals.md
python3 scripts/check_doc_freshness.py --base origin/main
python3 scripts/verify.py --full
```

Pass: verify exit 0; ARCHITECTURE row mentions Actuals table + Period.
