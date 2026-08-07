# Issue #225 — Order quality + inventory estimate overrides

Evidence tier: sandbox-e2e
scenario: order-quality-inventory-overrides

## Jam / §4 (approved 2026-08-06)

- Inventory: Estimations only; per base × Estimated delivery date; Manual incl. 0; batch drawer → one Save → one recompute. Actuals/CSV unchanged.
- Order Quality: one BarChartCard; Metric P95 | Average (`AVG(per_item_min)`); Aggregate | By-source grouped; drop dual lines / always-on median.
- Localhost review before PR open.
- Feature-flag decision: **no new flag** — cannot silently produce wrong tip/payroll numbers; overrides are explicit operator Apply (same pattern as usage-day overrides #194 / `FEATURES.writeInventoryDayOverrides` already gates inventory writes). Reuse existing `FEATURES.writeRestock` for drawer visibility. No `docs/FEATURE_FLAGS.md` entry.
- Model routing: Sonnet implement/UI; Composer docs.

### Per-scenario evidence (PR §4) — hosted screenshots required

1. **Happy — Inventory:** Drawer for Estimated date; set Ube Manual=0 (+ optional other); one Save; Ube=0; peers recompute; Source Manual on pinned rows.
2. **Happy — Clear:** Switch row back to Estimated; Save; rejoins water-fill.
3. **Happy — OQ Aggregate P95:** one bar chart + goal line.
4. **Happy — OQ Average + By-source:** Metric Average; breakdown By-source; grouped bars.
4b. **Happy — Aggregation Entire period:** `?grain=all` on Order Quality / Sales / Labor collapses Period into one bar/row labeled Entire period.
5. **Failure:** sum(manuals) > capacity → error, no write.
6. **Legacy:** Actuals date unchanged; restock CSV path intact; capacity edit still works.
7. Unit/structural + `python3 scripts/verify.py --full` green.
8. Post-merge: prod `/inventory` + `/order-quality` smoke.

## Citations

- `core/migrations/041_order_reco_delivery_date.sql` lines 39–143 (`tvf_order_reco_slot1` water-fill + actuals)
- `core/migrations/052_order_reco_n_slots.sql` lines 51–174 (`tvf_order_reco_slot_n`)
- `core/order_reco.py` lines 50–90 (`refresh_order_reco`)
- `apps/operator-console/lib/bq/writes.ts` lines 86–120 (`refreshOrderReco`); lines 38–44 (`clearRestockSchedule`)
- `apps/operator-console/lib/bq/queries.ts` lines 1159–1201 (`orderRecoSlots`); lines 906–928 (`orderQualityByGrain`); lines 878–892 (`kdsBySource`)
- `apps/operator-console/app/inventory/page.tsx` lines 32–82 (columns); lines 146–199 (page UI)
- `apps/operator-console/app/inventory/actions.ts` lines 165–199 (`applyUsageDayOverridesAction` batch pattern)
- `apps/operator-console/components/inventory/UsageDayOverrideDrawer.tsx` lines 51–100 (Sheet batch Apply)
- `apps/operator-console/app/order-quality/page.tsx` lines 213–232 (dual LineChartCard to replace)
- `apps/operator-console/app/sales/page.tsx` lines 208–255 (Composition Aggregate/By-source pills)
- `apps/operator-console/components/charts/BarChartCard.tsx` lines 189–300
- `apps/operator-console/components/tables/DataTable.tsx` lines 152–156 (source Badge — extend Manual)
- Docs lock-step: `docs/operator-console/ARCHITECTURE.md` Inventory + Order Quality; `agents/bhaga/knowledge-base/DOMAIN.md` order-reco section; run `python3 scripts/check_doc_freshness.py`. No RUNBOOK.md (Slack restock path unchanged). CONTRIBUTING.md §4 for PR evidence.

## Stubs

```sql
-- core/migrations/055_order_tub_overrides.sql
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_order_tub_overrides` (
  store STRING NOT NULL,
  delivery_date DATE NOT NULL,
  item STRING NOT NULL,
  quantity_tubs INT64 NOT NULL,
  updated_by STRING,
  updated_at TIMESTAMP
);
-- Recreate tvf_order_reco_slot1 + tvf_order_reco_slot_n:
-- when NOT has_actuals: overrides reduce water-fill budget; locked items excluded
-- from candidates; order_final uses override qty else est_selected.
```

```ts
export async function replaceOrderTubOverrides(
  store: string,
  deliveryDate: string,
  rows: { item: string; quantityTubs: number }[],
  by: string,
  opts?: { skipRefresh?: boolean },
): Promise<void>

export async function applyOrderTubOverridesAction(
  deliveryDate: string,
  rows: { item: string; quantityTubs: number }[], // empty = all Estimated
): Promise<ActionAck<OrderRecoQueuedMeta>>

export type OrderRecoSource = "Estimated" | "Manual" | "Actuals" | null;
```

```bash
python3 -m unittest core.test_migration_055_order_tub_overrides
cd apps/operator-console && npx vitest run __tests__/order-reco-pivot.test.ts __tests__/order-quality-chart.test.ts
BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
cd apps/operator-console && npm run dev
python3 scripts/verify.py --full
```

## Invariants

- Integer tubs; America/Chicago; Blade `order_tubs=0`; Actuals supersede overrides for whole date.
- Idempotent replace-per-date overrides; one `refresh_order_reco` per Apply.
- Source date-level Actuals detection unchanged; per-row Manual only when override row exists and not Actuals.
- Sandbox isolation N/A for console BQ writes (prod ADC localhost dogfood; same as prior inventory drawers).

## Milestone 1 — Migration + structural tests (Sonnet)

Add `055_order_tub_overrides.sql` (table + TVF slot1/slot_n override-aware water-fill). Add `core/test_migration_055_order_tub_overrides.py`. Apply via `ensure_schema()`.

**Verify:**
```bash
python3 -m unittest core.test_migration_055_order_tub_overrides
BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
```

## Milestone 2 — Inventory writes + drawer UI (Sonnet)

`replaceOrderTubOverrides` / clear on schedule delete; `applyOrderTubOverridesAction`; `EstimateTubsDrawer`; inventory page entry points; pivot + DataTable Manual badge; vitest.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/order-reco-pivot.test.ts
```

## Milestone 3 — Order Quality bar + metric/breakdown (Sonnet)

Extend queries with `kds_avg_min`; one BarChartCard; FilterPills Metric + View; vitest series helper.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/order-quality-chart.test.ts
```

## Milestone 4 — Docs + localhost + verify (Composer/Sonnet)

ARCHITECTURE + DOMAIN lock-step; localhost dogfood; `verify.py --full` before PR (after operator localhost OK).

**Verify:**
```bash
python3 scripts/check_doc_freshness.py
python3 scripts/verify.py --full
```

## Branch / PR mechanics

- Branch: `fix/order-quality-and-inventory-page-improvements` (already).
- All GitHub ops as `jarvis-agent-bot328`; never push `main`; never self-merge.
- `gh pr create --base main` only after localhost operator approval; `Closes #225`.
- Babysit via `pr_triage.py`; reply every thread; one push per review cycle.
- Cost: `pr_cost_ledger.py bind-pr` + `sync` after PR exists; do not commit zero build cost.
