# Issue #238 — Inventory restock: Move/Remove date + quick Actuals form

Evidence tier: sandbox-e2e
scenario: inventory-restock-move-remove-quick-form

## Jam / §4 (approved 2026-08-10)

- **Move date** (console-only): rekey schedule + actuals + tub overrides `from → to` (works when `from` has Actuals — fixes 8/17→8/20). If `to` already registered Estimated, attach Actuals there. Supersedes UI for Replace estimated.
- **Remove date** (console-only): DELETE schedule + actuals + overrides for a date; refresh reco.
- **Quick Actuals form**: Restock Sheet “Add order” opens with per-base qty inputs **prefilled from Estimated Order Tubs** for that delivery date; operator edits deltas; Submit → replace-per-date Actuals. CSV/photo remains optional Import.
- Scope: Operator Console only — Slack `/bhaga-cloud restock` unchanged.
- Feature flag: **no new flag** — explicit Submit behind `FEATURES.writeRestock`.
- **Remove date** worked for schedule/actuals/overrides; Order Tubs column lag on
  localhost was a console gap — restock writes skipped inline refresh and only
  enqueued Cloud Run. Fix: same `BYPASS_IAP` sync-refresh path as estimate pins
  (Remove/Move/Restock/Capacity + page `ensureOrderRecoFresh`).


### Per-scenario evidence (PR §4)

1. **Happy — Move Actuals:** Move disposable/far-future or 8/17→8/20; Actuals on `to`; `from` gone; Next delivery / Order reco update.
2. **Happy — Remove:** Remove a registered date; BQ schedule+orders+overrides empty; date off Next delivery.
3. **Happy — Quick form add:** Estimated date → form prefilled with Estimate Order Tubs → tweak → Submit → Source Actuals.
4. **Happy — Quick form update:** Re-open Actuals date; edit qty → replace-per-date converges.
4b. **Happy — Order tubs Actuals click:** Click Order tubs on an Actuals column → Edit actuals Sheet → change qty → Apply → Source stays Actuals; BQ orders updated.
5. **Legacy:** CSV/photo import; capacity; Manual estimate pins; register-only; reset-to-estimated.
6. **Failure:** Move `from===to` / unknown `from` rejected; Remove without confirm → no write; negative/non-integer qty rejected.
7. Vitest + `python3 scripts/verify.py --full` green.
8. Post-merge: prod `/inventory` smoke (move 8/17→8/20 if still wrong).

## Citations

- `apps/operator-console/lib/bq/writes.ts` L18–50 (`setRestockSchedule`, `clearRestockOrders`, `clearRestockSchedule`); L121–144 (`replaceRestockOrders`); L254–323 (`RestockAction`, `submitRestock`, `replaceEstimatedRestockDate`)
- `apps/operator-console/lib/bq/queries.ts` L1731–1820 (`orderRecoSlots`, `nextDates`, `estimatedScheduleDates`)
- `apps/operator-console/app/inventory/actions.ts` L43–85 (`submitRestockAction`, `replaceEstimatedRestockDateAction`)
- `apps/operator-console/components/drawers/RestockImportDrawer.tsx` L29–34 (`ACTION_LABELS`); L240–258 (CSV-only add-order)
- `apps/operator-console/components/inventory/EstimateTubsDrawer.tsx` L40–52, L135–228 (Sheet batch form pattern to mirror)
- `apps/operator-console/app/inventory/page.tsx` L95–105 (RestockImportDrawer props)
- `apps/operator-console/lib/restock/parse.ts` L4 (`ACTIVE_BASES`)
- Docs lock-step: `docs/operator-console/ARCHITECTURE.md` §5–§6 restock actions; `docs/operator-console/EXECUTION.md` M3; `check_doc_freshness.py`. No RUNBOOK. `docs/contributing/ui-polish.md` (Sheet/Input/Button + pending states). CONTRIBUTING.md §4.

## Stubs

```ts
/** Move schedule (+ actuals + overrides) from→to; refresh reco. */
export async function moveRestockDate(
  store: string, fromDate: string, toDate: string, by: string,
  opts?: RecoRefreshOpts,
): Promise<void>

/** Remove a registered date entirely; refresh reco. */
export async function removeRestockDate(
  store: string, deliveryDate: string, by: string,
  opts?: RecoRefreshOpts,
): Promise<void>

/** Future schedule dates (Estimated or Actuals) for Move/Remove pickers. */
export function scheduledRestockDates(store: string): Promise<{
  delivery_date: string; has_actuals: boolean;
}[]>

export async function moveRestockDateAction(fromDate: string, toDate: string): Promise<ActionAck<OrderRecoQueuedMeta>>
export async function removeRestockDateAction(deliveryDate: string): Promise<ActionAck<OrderRecoQueuedMeta>>

export type RestockAction =
  | "add-order" | "register-only" | "reset-to-estimated"
  | "move-date" | "remove-date" | "replace-estimated"; // replace kept for back-compat tests → delegates to move for Estimated-only or removed from UI
```

```bash
cd apps/operator-console && npx vitest run __tests__/restock-replace.test.ts __tests__/restock-import-drawer.test.tsx
python3 scripts/verify.py --full
cd apps/operator-console && npm run dev   # localhost dogfood before PR
```

## Invariants

- Idempotent DELETE/MERGE; no orphan actuals/overrides after Move/Remove.
- Move reads actuals **before** clearRestockSchedule(from); writes to `to` via replaceRestockOrders.
- Integer tubs ≥ 0; America/Chicago schedule window (same as estimatedScheduleDates).
- Slack path untouched; async order-reco enqueue when `FEATURES.asyncOrderReco`.
- Dual-date reco refresh after every write.

## UX polish (ui-polish.md)

- Reuse: shadcn `Sheet`/`Select`/`Input`/`Button`/`Label`, `useConsoleAction`, muted description text — same as EstimateTubsDrawer / RestockImportDrawer.
- States: Submit/Remove disabled while pending; confirm checkbox for Remove; focus-visible on inputs; h-10 (~40px) tap targets; mobile Sheet `w-full sm:max-w-md`.
- Visual polish is §4 bar (localhost + later hosted screenshots).

## Milestone 1 — Write path (Sonnet)

Add `moveRestockDate`, `removeRestockDate` in `writes.ts`; `scheduledRestockDates` in `queries.ts`; server actions in `actions.ts`. Keep `replaceEstimatedRestockDate` (Estimated-only) or route UI to Move.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/restock-replace.test.ts
```
Pass: Move Actuals happy + reject same-date; Remove clears schedule+orders; existing replace tests still green.

## Milestone 2 — UI form + Move/Remove (Sonnet)

Update `RestockImportDrawer`: Move/Remove actions; Add order shows ACTIVE_BASES form prefilled from `estimateByDate` prop; optional CSV/photo Import. Wire props from `inventory/page.tsx`.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/restock-import-drawer.test.tsx
```
Pass: form visible without file; Move/Remove fields; sample CSV still under Import.

## Milestone 3 — Docs + verify (Composer)

ARCHITECTURE §5/§6 + EXECUTION M3; `check_doc_freshness.py`; `verify.py --full`.

**Verify:**
```bash
python3 scripts/verify.py --full
```

## Milestone 4 — Localhost dogfood then PR (Sonnet)

`npm run dev` — operator reviews Move 8/17→8/20 + form on localhost **before** `gh pr create`. Then §4 screenshots to evidence-screenshots; babysit; never self-merge.

**Verify:**
```bash
python3 scripts/check_evidence_readiness.py --pr N
```

## Branch / PR mechanics

- Branch: `fix/need-to-work-on-some-improvemnts` · Closes #238 · `gh pr create --base main`
- Bot push; babysit; operator squash-merges; cost bind-pr + sync.
