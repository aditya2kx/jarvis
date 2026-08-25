# Issue #261 — Inventory reco: no torn numbers while refresh runs

Evidence tier: sandbox-e2e

Jam / §4 approved 2026-08-24 in chat (operator: aligned, start building).

## Goal

Registering an estimated date (or any async `order-reco` enqueue) must not paint
Order tubs / Avg/day / TOTAL from a half-written `inventory_order_reco`. Page
open must not re-enqueue while a job is in flight, and must poll instead of
asking for a manual reload.

## Citations

- `apps/operator-console/app/inventory/page.tsx` lines 88–99, 193–197 (`recoQueued` static banner; `dates` from live `nextDates()`)
- `apps/operator-console/lib/bq/writes.ts` lines 154–193 (`refreshOrderReco` DELETE-then-INSERT)
- `apps/operator-console/lib/inventory/orderRecoPivot.ts` lines 40–75 (last-write-wins per cell; no generation)
- `apps/operator-console/lib/inventory/useOrderRecoRefreshFollowup.ts` lines 82–97 (`router.refresh()` immediately)
- `apps/operator-console/lib/bhaga/recompute.ts` lines 108–111 (`triggerOrderRecoRefresh` always POSTs `:run`)
- `core/order_reco.py` lines 50–90 (same DELETE-first)
- `cloud/webhook/handler.py` `_refresh_order_reco` lines 1310–1379
- `core/migrations/055_order_tub_overrides.sql` `s_prev` lines 155–166 (no `QUALIFY` on `refreshed_at`)
- `docs/operator-console/ARCHITECTURE.md` §6 dual-date table
- `docs/contributing/ui-polish.md` (muted banner, `role="status"`, existing border tokens)

## Feature-flag decision

No new flag. Reuse `FEATURES.asyncOrderReco`. Wrong Order tubs during refresh
can silently look like a math bug — the fix is to refuse torn paint, not to
gate the water-fill formula.

## Invariants

- Integer tubs; America/Chicago dates; Blade `order_tubs = 0`; Actuals win over Manual.
- Idempotent reco write: one `refreshed_at` generation per completed refresh; old rows deleted only after the new generation is fully inserted.
- Never retry enqueue while a `bhaga-daily-refresh` execution is already running (side-effect guard).
- Console `/inventory` table `SUM(item Order Tubs)` equals TOTAL for every painted date, or that date is omitted.

## Milestone 1 — Paint only a consistent generation

Add `selectPaintGeneration` + `completeDatesForGeneration` in
`orderRecoPivot.ts`. Group long rows by `refreshed_at`; a date is complete when
a TOTAL row exists and `SUM(Order Tubs)` over Item ≠ TOTAL equals TOTAL.
Score = count of complete dates ∩ live next-dates; pick highest score, then
latest timestamp. Page passes **those** dates into `OrderRecoTable`, not raw
`nextDates()`. Restock drawer still gets live dates.

Include `refreshed_at` on `orderRecoSlots()` (`queries.ts` ~1770).

**Verify:** `cd apps/operator-console && npx vitest run __tests__/order-reco-pivot.test.ts`

## Milestone 2 — Write-then-swap refresh + slot_n QUALIFY

`core/migrations/067_order_reco_write_then_swap.sql`: `CREATE OR REPLACE`
`tvf_order_reco_slot_n` identical to 055 plus
`QUALIFY ROW_NUMBER() OVER (PARTITION BY Item ORDER BY refreshed_at DESC) = 1`
on `s_prev`.

`refresh_order_reco` / `refreshOrderReco` / `_refresh_order_reco`: bind one
`gen` TIMESTAMP; INSERT slot1 then slot_n with that `gen`; then
`DELETE … WHERE store = @store AND refreshed_at != @gen`. Empty next-dates
still DELETEs all rows.

**Verify:** `python3 -m unittest core.test_order_reco core.test_migration_067_order_reco_write_then_swap`

## Milestone 3 — Dedupe enqueue + poll, no reload banner

`hasRunningBhagaJob()` lists Cloud Run executions without `completionTime`;
`triggerOrderRecoRefresh` no-ops if one is running (still treat as queued for
poll). `useOrderRecoRefreshFollowup({ skipImmediateRefresh })` for page-open.
`InventoryRecoFreshness` client: muted `role="status"` banner; poll until
paint-ready (live dates all complete in chosen generation). Timeout uses
existing error toast.

**Verify:** `npx vitest run __tests__/ensure-order-reco-fresh.test.ts __tests__/recompute.test.ts` plus follow-up hook test; `python3 scripts/verify.py --full`

## Per-scenario evidence (PR §4)

1. **Happy — already fresh:** `/inventory` no pending banner.
2. **Happy — register 9/4:** live schedule has the date; table does not show 9/4 Order tubs until generation is complete; then SUM(items)=TOTAL.
3. **Happy — poll:** numbers appear without a manual reload.
4. **Failure — timeout:** existing timeout toast; no enqueue storm.
5. **Legacy:** drawers still `followOrderReco`; 8/28 Actuals unchanged; Blade 0.
6. Hosted screenshots: `python3 apps/operator-console/scripts/capture_evidence.py --path /inventory --label reco-fresh` and `--label reco-pending` as needed.

## Docs lock-step

`docs/operator-console/ARCHITECTURE.md` §6 freshness; `DOMAIN.md` write-then-swap;
`FEATURE_FLAGS.md` async-reco row (poll, not “reload”); `MUTATING_ACTIONS.md`;
`core/order_reco.py` docstring; `python3 scripts/check_doc_freshness.py`.

## Branch / PR

`fix/want-to-work-on-some-improvements` → `gh pr create --base main` as
`jarvis-agent-bot328`; Closes #261; never self-merge.

## Model routing

M1–M3 Sonnet/Composer in this chat (one PR).
