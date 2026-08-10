# Issue #240 — Deploy traffic → latest + editable Current Qty

Evidence tier: sandbox-e2e
scenario: inventory-current-qty-override-deploy-traffic

## Jam / §4 (approved 2026-08-10)

1. **Deploy:** after `gcloud run deploy` in `operator-console-deploy.yml`, force
   `gcloud run services update-traffic … --to-latest` so sticky tags (e.g. `pr234`)
   never leave a new revision at 0% (incident: #239 / `00105` at 0%).
2. **Current Qty:** sticky override table + COALESCE in `vw_inventory_order_assistant`;
   Inventory click → Sheet → MERGE → refresh reco (same BYPASS_IAP sync path as tubs).
3. **Feature-flag decision:** no new flag — reuse `FEATURES.writeRestock` (same write
   class as Order Tubs; wrong Current Qty can silently skew reco, so writes stay
   behind the existing write gate; FEATURE_FLAGS.md unchanged).
4. **Docs lock-step:** `docs/operator-console/EXECUTION.md` §5.4;
   `docs/operator-console/ARCHITECTURE.md` inventory Current Qty note;
   `RUNBOOK.md` operator-console deploy note if traffic step is mentioned;
   `python3 scripts/check_doc_freshness.py` (doc-maintenance).
5. **Branch/PR:** `fix/i240-deploy-traffic-editable-current-qty` → `gh pr create --base main` as
   `jarvis-agent-bot328`; never self-merge; reply every review thread; babysit to green.
6. **Model routing:** M1 Composer; M2 Sonnet; M3 Sonnet (one chat per PR).

### Per-scenario evidence (PR §4)

1. **Happy — edit Current Qty:** localhost `/inventory` → click Current Qty for a base →
   change qty → Apply → table shows new value; BQ row in `inventory_current_qty_overrides`.
2. **Happy — reset:** Clear override → qty returns to ClickUp closing; override row gone.
3. **Happy — reco math:** On-hand / Order Tubs recompute after refresh (override feeds OA `current_qty`).
4. **Deploy YAML:** workflow contains `update-traffic` + `--to-latest` after deploy step.
5. **Legacy:** Order Tubs / Move-Remove / usage-day overrides unchanged.
6. **Failure:** negative qty rejected; TOTAL/Blade not editable.
7. Vitest + `python3 scripts/verify.py --full` green.

## Citations

- `.github/workflows/operator-console-deploy.yml` lines 76–87 (deploy step; add traffic)
- `docs/operator-console/EXECUTION.md` lines 404–411 (§5.4 deploy)
- `core/migrations/048_inventory_usage_day_overrides.sql` lines 154–165 (`latest_reading`)
- `core/migrations/055_order_tub_overrides.sql` lines 10–17 (override table pattern)
- `apps/operator-console/lib/bq/writes.ts` line 908 (`setUsageDayOverride` MERGE pattern)
- `apps/operator-console/app/inventory/actions.ts` lines 266–288 (`applyOrderTubOverridesAction`)
- `apps/operator-console/components/inventory/OrderRecoTable.tsx` lines 60–64 (Current Qty col)
- `docs/contributing/ui-polish.md` (Sheet/Input/Button)

## Stubs

```sql
-- core/migrations/058_inventory_current_qty_overrides.sql
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_current_qty_overrides` (
  store STRING NOT NULL, item STRING NOT NULL,
  quantity_units FLOAT64 NOT NULL,
  updated_by STRING, updated_at TIMESTAMP
);
-- CREATE OR REPLACE VIEW vw_inventory_order_assistant: COALESCE(ov.quantity_units, quantity_units)
```

```ts
export async function setCurrentQtyOverride(
  store: string, item: string, quantityUnits: number, by: string,
  opts?: RecoRefreshOpts,
): Promise<void>
export async function clearCurrentQtyOverride(
  store: string, item: string, opts?: RecoRefreshOpts,
): Promise<void>
export async function setCurrentQtyOverrideAction(
  item: string, quantityUnits: number,
): Promise<ActionAck<OrderRecoQueuedMeta>>
export async function clearCurrentQtyOverrideAction(
  item: string,
): Promise<ActionAck<OrderRecoQueuedMeta>>
```

```bash
cd apps/operator-console && npx vitest run __tests__/current-qty-override.test.ts
python3 scripts/verify.py --full
cd apps/operator-console && npm run dev   # http://localhost:3000/inventory
```

## Invariants

- Idempotent MERGE/DELETE; America/Chicago unchanged.
- Override sticky until cleared; ClickUp ingest does not wipe override table.
- Integer/float qty ≥ 0; Blade/TOTAL excluded from UI.
- `refresh_order_reco` / `order_reco.py` body unchanged — call refresh after write.
- Tagged Cloud Run revisions may remain for preview URLs; % traffic always 100% latest.

## UX polish

- Reuse shadcn Sheet/Input/Button/Label + `useConsoleAction` + refresh followup.
- Pending disable; focus-visible; `w-full sm:max-w-md`.

## Milestone 1 — Deploy traffic (Composer)

**Model:** Composer / fast.

Add post-deploy `update-traffic --to-latest` in
`.github/workflows/operator-console-deploy.yml`; note in `EXECUTION.md` §5.4.

**Verify:**
```bash
rg -n 'to-latest|update-traffic' .github/workflows/operator-console-deploy.yml docs/operator-console/EXECUTION.md
```
Pass: both files mention `--to-latest`.

## Milestone 2 — BQ override + writes (Sonnet)

**Model:** Sonnet.

Migration `058_inventory_current_qty_overrides.sql` (table + OA view COALESCE).
`setCurrentQtyOverride` / `clearCurrentQtyOverride` in `writes.ts`; actions + registry.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/current-qty-override.test.ts
BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
```
Pass: unit tests green; migration applied (or already applied).

## Milestone 3 — UI + dogfood (Sonnet)

**Model:** Sonnet.

`CurrentQtyDrawer` + clickable Current Qty in `OrderRecoTable`; ARCHITECTURE note.
Localhost dogfood.

**Verify:**
```bash
cd apps/operator-console && npx vitest run
python3 scripts/verify.py --full
cd apps/operator-console && npm run dev
```
Pass: edit + reset on `http://localhost:3000/inventory`.
