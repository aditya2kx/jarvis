# Base runway dual restock dates (Operator Console)

Evidence tier: sandbox-e2e
scenario: inventory-base-runway-dual

## Jam / §4 (approved)

- Scope: Base runway table only — dual-date reco / Next delivery / restock drawer unchanged.
- Restock 1/2 = same global slots as `vw_order_reco_next_dates` (schedule Estimated or Actuals).
- Qty 1/2 + Status 1/2 = Actuals-only Fine (preserve #156).
- Stockout 1 = burn-down from today; Stockout 2 = chain after D1 Actuals qty.
- Feature flag: none (same pattern as #157).
- Model routing: Sonnet for implement/UI/evidence; Composer for docs.

### Per-scenario evidence (PR §4) — hosted screenshots required

Happy path + failure/edge + legacy; pass criterion = each screenshot URL renders in PR §4 and unit tests green.

1. **Happy path — Screenshot A:** Restock 1/2 equal Next delivery D1/D2; dual column headers visible.
2. **Happy path — Screenshot B:** Status 1 Fine + Qty 1 populated for at least one Actuals base.
3. **Failure / edge — Screenshot C:** Estimated slot 2 → Restock 2 date, Qty 2 empty, Status 2 Risky (no Actuals recovery until upload).
4. **Legacy — Screenshot D:** Dual-date reco + Next delivery subtitle intact on `/inventory`.
5. Unit/structural tests + `python3 scripts/verify.py --full` green (exit criteria).
6. Post-merge: prod `/inventory` re-check Restock 1/2 vs Next delivery.

## Citations

- `core/migrations/035_inventory_base_runway.sql` lines 19–86 (`vw_inventory_base_runway`)
- `core/migrations/031_order_reco_dual.sql` lines 76–89 (`vw_order_reco_next_dates`); lines 99–106 (on_hand_arrival)
- `apps/operator-console/lib/bq/queries.ts` lines 531–547 (`BaseRunwayRow`, `baseRunway`)
- `apps/operator-console/lib/inventory/runway.ts` lines 1–37 (`runwayStatus`, `stockoutDateFromDaysLeft`)
- `apps/operator-console/app/inventory/page.tsx` lines 56–69 (runway columns), 132–146 (Base runway UI)
- `docs/operator-console/ARCHITECTURE.md` lines 245–251 (Base runway §6)
- Docs lock-step: `docs/operator-console/ARCHITECTURE.md` §6; run `python3 scripts/check_doc_freshness.py`. No RUNBOOK.md (Slack/cloud pipeline unchanged). CONTRIBUTING.md §4 applies for PR evidence.

## Stubs

```sql
-- core/migrations/036_inventory_base_runway_dual.sql
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_inventory_base_runway` AS ...
-- columns: Base, Stock, Vel per day, Days left,
--   Stockout 1, Restock 1, Qty 1, Status 1,
--   Stockout 2, Restock 2, Qty 2, Status 2
```

```ts
export type RunwayStatus = "Risky" | "Fine";
export function runwayStatus(stockoutDate: string | null, nextRestockDate: string | null): RunwayStatus
export function stockoutDateFromDaysLeft(daysLeft: number | null, todayIso: string): string | null
export function stockout2AfterSlot1(
  currentQty: number, vel: number, d1Iso: string, qty1: number | null, todayIso: string,
): string | null
export interface BaseRunwayRow {
  Base: string; Stock: number; "Vel per day": number; "Days left": number | null;
  "Stockout 1": string | null; "Restock 1": string | null; "Qty 1": number | null;
  "Status 1": "Risky" | "Fine";
  "Stockout 2": string | null; "Restock 2": string | null; "Qty 2": number | null;
  "Status 2": "Risky" | "Fine" | null;
}
```

```bash
cd apps/operator-console && npx vitest run __tests__/runway.test.ts
python3 -m unittest core.test_migration_036_inventory_base_runway_dual
python3 scripts/verify.py --full
```

## Invariants

- Idempotent view replace; America/Chicago for today / schedule filter.
- Actuals-only Status Fine; Estimated dates appear in Restock columns but cannot make Fine alone.
- Days left burn-down unchanged (ignores future restocks).
- Blade excluded; integer cents N/A (tubs floats OK); sandbox isolation N/A (console BQ view).
- Dual-date reco / Slack restock path untouched.

## Milestone 1 — BQ view + structural tests (Sonnet)

Add `036_inventory_base_runway_dual.sql` replacing `vw_inventory_base_runway` with dual-slot columns; join `vw_order_reco_next_dates` + Actuals qty; Stockout 2 chain CTE. Add `core/test_migration_036_inventory_base_runway_dual.py`.

**Verify:**
```bash
python3 -m unittest core.test_migration_036_inventory_base_runway_dual
```

## Milestone 2 — Console types + UI + TS helpers (Sonnet)

Update `BaseRunwayRow`, `runway.ts` (`stockout2AfterSlot1`), inventory page columns/blurb/row highlight (Status 1 OR Status 2 Risky), `runway.test.ts`.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/runway.test.ts
```

## Milestone 3 — Docs + verify (Composer)

Update ARCHITECTURE.md §6 Base runway; `check_doc_freshness.py`; `verify.py --full`.

**Verify:**
```bash
python3 scripts/verify.py --full
```

## Milestone 4 — §4 screenshots + PR (Sonnet)

Playwright `/inventory` screenshots A–D → GitHub hosted URLs; apply migration to prod BQ; PR `--base main` Refs #164; babysit; never self-merge.

**Verify:**
```bash
python3 scripts/check_evidence_readiness.py --pr N
```

## Branch / PR mechanics

- Branch: `fix/i164-update-https-operator-console-887772634501-us`
- One branch = one PR; bot push; babysit; operator squash-merges.
- Cost: bind-pr + pr_cost_ledger sync after PR open.
