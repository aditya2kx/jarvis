# Sales page: multi-select source filter + breakdown toggle (#198)

Evidence tier: sandbox-e2e
(+ mandatory prod-live Operator Console screenshots — portal path; G5)

## Jam / §4 (approved 2026-07-26)

- Multi-select filter on raw `square_transactions.source` values on `/sales`.
- Breakdown toggle: off = aggregated bars for selected sources; on = stacked per source.
- Orders & items: separate bar charts (not line); same filter + breakdown.
- Data: `square_transactions` + `square_item_lines` join for items; leave `vw_model_labor_daily` for Home/Labor.
- No DoorDash/Storefront collapse; no feature flag (display-only; cannot silently wrong payroll).
- Goal line only when day grain + all sources selected (unfiltered).

### Per-scenario evidence (PR §4)

1. **E1 Happy — unfiltered reconcile:** BQ rollup from transactions equals `vw_model_labor_daily` net_sales/orders for ≥3 recent open days.
2. **E2 Happy — multi-select filter:** `capture_evidence.py --path '/sales?…&sources=DoorDash,Uber+Eats'` hosted screenshot.
3. **E3 Happy — aggregate vs breakdown:** screenshots `breakdown=0` and `breakdown=1`; stacks sum to aggregate.
4. **E4 Happy — bar charts:** Orders + Items are bar charts with stacked breakdown.
5. **E5 Default / legacy:** bare `/sales?range=30d&grain=day` matches full-store totals + day goal line.
6. **E6 Unit:** parser + pivot + query filter tests; `verify.py --full` green.
7. **E7 Docs:** ARCHITECTURE/EXECUTION Sales row updated; `check_doc_freshness.py` clean.

## Citations

- `apps/operator-console/app/sales/page.tsx` lines 1-127 (current Period/grain-only page; `laborByGrain` + LineChartCard)
- `apps/operator-console/lib/bq/queries.ts` lines 53-79 (`laborByGrain`), lines 497-518 (`orderQualityByGrain` source-param pattern)
- `apps/operator-console/components/charts/BarChartCard.tsx` lines 104-131 (`stacked`, `headerRight`)
- `apps/operator-console/components/filters/FilterSelect.tsx` lines 22-66; `FilterPills.tsx` lines 19-59
- `apps/operator-console/components/tables/DataTable.tsx` lines 169-275 (`MultiSelectFilter` checkbox UX to mirror)
- `apps/operator-console/lib/filters/range.ts` lines 54-64 (`firstValue`), line 219 (`parseGrain`)
- `agents/bhaga/knowledge-base/DOMAIN.md` lines 109-145 (`square_transactions.source`, `item_lines.channel`)
- `docs/contributing/sandbox-evidence.md` lines 25-28 (portal screenshots required)
- `docs/operator-console/ARCHITECTURE.md` line 163; `docs/operator-console/EXECUTION.md` line 571
- Branch: `fix/i198-improving-adding-filters-on-the-sales`; bot `jarvis-agent-bot328`; `gh pr create --base main`; never self-merge; babysit skill

## Concrete stubs / CLI

```ts
// lib/filters/sources.ts
export function parseSources(value: string | string[] | undefined): string[] | null
// null = all sources; [] never — empty param treated as all
export function serializeSources(sources: string[] | null): string // "" when all
export function parseBreakdown(value: string | string[] | undefined): boolean

// lib/bq/queries.ts
export interface SalesBySourceRow {
  date: string;
  source: string | null; // null when not grouping by source
  net_sales: number;
  orders: number;
  items_sold: number;
  avg_order_price: number;
}
export function salesByGrain(
  win: DateWindow,
  grain: Grain,
  sources: string[] | null, // null = all
  bySource: boolean,
): Promise<SalesBySourceRow[]>
export function salesSourceOptions(win: DateWindow): Promise<string[]>
```

```sql
-- Aggregate (bySource=false): GROUP BY date bucket only
-- Breakdown (bySource=true): GROUP BY date bucket, source
-- Filter: (@all_sources OR source IN UNNEST(@sources))
-- net_sales = SUM(net_sales_cents)/100
-- orders = COUNTIF(event_type = 'Payment')
-- items_sold = SUM of qty_sold from item_lines JOIN transactions (Payment lines)
```

```bash
python3 scripts/check_plan_readiness.py --plan docs/plans/i198-sales-source-filters.md
cd apps/operator-console && npm test -- sales-sources
python3 scripts/verify.py --full
python3 apps/operator-console/scripts/capture_evidence.py --path '/sales?range=30d&grain=day&sources=DoorDash,Uber+Eats&breakdown=1' --label sales-break-dd-ue
gh pr create --base main --head fix/i198-improving-adding-filters-on-the-sales …
```

## Invariants

- America/Chicago date boundaries via existing `resolvePageRange` / `date_local`.
- Read-only BQ; no writes to sheets/Firestore; no tip/payroll path.
- Unfiltered totals must reconcile to `vw_model_labor_daily` (backward compatible default).
- Source names bound as params (`UNNEST(@sources)`), never string-interpolated.
- Integer cents in BQ; dollars only at display (`/100`).
- Idempotent reads only — no upserts.

## Feature-flag decision

**No new flag.** Additive UI filter on a display-only console page; cannot silently produce wrong tip/payroll numbers. Default URL (no `sources`/`breakdown`) preserves existing full-store behavior.

## Docs lock-step

- `docs/operator-console/ARCHITECTURE.md` — Sales row: source multi-select + breakdown
- `docs/operator-console/EXECUTION.md` — Sales Grafana parity row if needed
- `python3 scripts/check_doc_freshness.py`
- `PROGRESS.md` via follow-up retro PR only (never direct main)

## Branch / PR mechanics

- One branch `fix/i198-improving-adding-filters-on-the-sales` → one PR `--base main`
- All GitHub ops as `jarvis-agent-bot328`; never self-merge; babysit after push
- Refs #198; bind cost ledger after `gh pr create`

## Model routing

- M1–M3: Sonnet (feature work)
- Plan review / hard bugs only: Opus
- Doc polish: Composer/Sonnet

---

## Milestone 1 — Filter parsing + multi-select UI (Sonnet)

Add `lib/filters/sources.ts` + `components/filters/FilterMultiSelect.tsx` (checkbox dropdown, URL `sources=a,b`). Add `FilterPills` breakdown Aggregate/Breakdown on chart headers via `BarChartCard.headerRight` or page header.

**Verify:** `npm test -- sales-sources` — parseSources/serialize/breakdown cases pass.

## Milestone 2 — BQ salesByGrain + Sales page rewrite (Sonnet)

Add `salesByGrain` / `salesSourceOptions` in `queries.ts`. Rewrite `sales/page.tsx`: three `BarChartCard`s (net sales / orders / items); extend `BarValueFormat` with `"number"` for count charts; goal only unfiltered day grain; detail table follows filter (aggregate rows; when breakdown, optionally show Source column — prefer aggregate table always for clarity, charts do breakdown).

**Verify:** local page load or unit test of SQL param shape; unfiltered totals match model for a known day via a small vitest fixture of the pivot helper.

## Milestone 3 — Docs + evidence screenshots (Sonnet)

Update ARCHITECTURE/EXECUTION. Capture E2–E4 screenshots. Assemble PR §4. `verify.py --full`.

**Verify:** `check_doc_freshness.py` clean; hosted https screenshot URLs in PR body.

## Collected evidence (2026-07-26)

### Filtered DoorDash (control = direct txn GROUP BY)

```
| date       | net    | orders | items |
| 2026-07-22 | 208.29 |     10 |    14 |
| 2026-07-23 | 148.34 |      8 |    11 |
| 2026-07-24 | 182.95 |      7 |    13 |
```

### Breakdown sum == aggregate (DoorDash+Uber Eats)

```
| date       | agg_net | brk_net | net_match | orders_match |
| 2026-07-22 |  312.78 |  312.78 | true      | true         |
| 2026-07-23 |  259.82 |  259.82 | true      | true         |
| 2026-07-24 |  361.09 |  361.09 | true      | true         |
```

### Week grain == sum of day grain (2026-07-20 week)

```
| week       | week_net | days_sum_net | week_orders | days_sum_orders | match |
| 2026-07-20 |  9624.32 |      9624.32 |         609 |             609 | true  |
```
