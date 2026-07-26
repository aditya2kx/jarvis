# Sales page: source filters + Composition/Trend + shared time (#198)

Evidence tier: sandbox-e2e
(+ mandatory prod-live Operator Console screenshots — portal path; G5)

## Jam / §4 (approved 2026-07-26; extended Composition/Trend + shared time same day)

- Multi-select filter on raw `square_transactions.source` values on `/sales`.
- **Composition** mode: bar charts; View Aggregate / By source (`breakdown`).
- **Trend** mode: line charts; Compare prior off/on (`compare`); prior = equal-length window ending day before current start.
- Mutual exclusion: Breakdown only in Composition; Compare only in Trend; mode switch clears the other.
- Shared time filters: Period (`oc_range` + custom `oc_from`/`oc_to`) and Aggregation (`oc_grain`) persist across Home + Performance pages that expose them.
- Data: `square_transactions` + `square_item_lines` join for items; leave `vw_model_labor_daily` for Home/Labor.
- No DoorDash/Storefront collapse; no feature flag (display-only; cannot silently wrong payroll).
- Goal line only Composition + day grain + all sources + no breakdown.

## Citations

- [`docs/operator-console/ARCHITECTURE.md`](docs/operator-console/ARCHITECTURE.md) lines 165–165 (Sales row) and lines 363–393 (§12 date range + Composition/Trend + cookies)
- [`docs/operator-console/EXECUTION.md`](docs/operator-console/EXECUTION.md) lines 459–475 (§5.5c) and line 571 (Grafana Sales parity)
- `apps/operator-console/app/sales/page.tsx` — Composition/Trend + Source + shared filters
- `apps/operator-console/lib/filters/chart-mode.ts` — `parseChartMode` / `parseCompare` / `assertModeFilterCoherence`
- `apps/operator-console/lib/filters/period.ts` — `resolvePageRange` / `resolvePageGrain` cookies
- `apps/operator-console/lib/filters/range.ts` — `GRAIN_COOKIE` / `FROM_COOKIE` / `TO_COOKIE` / `priorWindow`
- `apps/operator-console/lib/charts/sales-pivot.ts` — `pivotSalesChart` / `mergePriorSeries`
- `apps/operator-console/lib/bq/queries.ts` — `salesByGrain` / `salesSourceOptions`
- `apps/operator-console/components/filters/FilterSelect.tsx` — Period + grain cookie writes
- `apps/operator-console/components/filters/DateRangePicker.tsx` — custom from/to cookie writes
- Branch: `fix/i198-improving-adding-filters-on-the-sales`; bot `jarvis-agent-bot328`; PR `--base main`; never self-merge; babysit skill

### Per-scenario evidence (PR §4)

Happy path + failure/recovery covered below (pass criterion: each scenario verified before merge).

1. **E1 Happy path — unfiltered reconcile:** BQ rollup from transactions equals `vw_model_labor_daily` net_sales/orders for ≥3 recent open days.
2. **E2 Happy path — multi-select filter:** `capture_evidence.py --path '/sales?…&sources=DoorDash,Uber+Eats'` hosted screenshot.
3. **E3 Happy path — aggregate vs breakdown:** screenshots `breakdown=0` and `breakdown=1`; stacks sum to aggregate.
4. **E4 Happy path — bar charts:** Orders + Items are bar charts with stacked breakdown (Composition).
5. **E5 Default / legacy:** bare `/sales?range=30d&grain=day` matches full-store totals + day goal line.
6. **E6 Unit:** parser + pivot + query filter tests; `verify.py --full` green.
7. **E7 Docs:** ARCHITECTURE/EXECUTION Sales + §12 updated; `check_doc_freshness.py` clean.
8. **E8 Shared time:** Period + Aggregation cookies; Home inherits Period; Sales/Labor inherit grain.
9. **E9 Composition breakdown:** screenshot `mode=composition&breakdown=1`.
10. **E10 Trend + compare:** screenshot `mode=trend&compare=1` (lines + dashed prior).
11. **E11 Failure/recovery — mutual exclusion:** illegal URL with both gated flags coerces; UI never shows both View and Compare; empty Source shows recovery empty-state (not a BQ error).
12. **E12 Unit:** chart-mode / priorWindow / period-grain cookie tests; `verify.py --full` green.

```ts
// lib/filters/chart-mode.ts
export type ChartMode = "composition" | "trend"
export function parseChartMode(value: string | string[] | undefined): ChartMode
export function parseCompare(value: string | string[] | undefined): boolean
export function assertModeFilterCoherence(
  mode: ChartMode, breakdown: boolean, compare: boolean,
): { mode: ChartMode; breakdown: boolean; compare: boolean }

// lib/filters/range.ts
export const GRAIN_COOKIE = "oc_grain"
export const FROM_COOKIE = "oc_from"
export const TO_COOKIE = "oc_to"
export function priorWindow(win: DateWindow): DateWindow

// lib/filters/period.ts
export async function resolvePageRange(...): Promise<DateWindow>
export async function resolvePageGrain(...): Promise<Grain>

// lib/charts/sales-pivot.ts
export function mergePriorSeries(current, prior, metricKey, priorLabel?): ...
```

```bash
python3 scripts/check_plan_readiness.py --plan docs/plans/i198-sales-source-filters.md
cd apps/operator-console && npm test -- --run sales-chart-mode period-grain sales-sources
python3 scripts/verify.py --full
python3 apps/operator-console/scripts/capture_evidence.py --path '/sales?range=30d&grain=day&mode=composition&breakdown=1' --label sales-composition-break
python3 apps/operator-console/scripts/capture_evidence.py --path '/sales?range=30d&grain=day&mode=trend&compare=1' --label sales-trend-compare
```

## Invariants

- America/Chicago date boundaries via existing `resolvePageRange` / `date_local`.
- Read-only BQ; no writes to sheets/Firestore; no tip/payroll path.
- Unfiltered Composition totals must reconcile to `vw_model_labor_daily` (backward compatible default).
- Source names bound as params (`UNNEST(@sources)`), never string-interpolated.
- Integer cents in BQ; dollars only at display (`/100`).
- Idempotent reads only — no upserts.
- Labor/Forecast/Inventory are not converted to Composition/Trend in this PR — only shared time cookies.

## Feature-flag decision

**No new flag.** Additive UI filter on a display-only console page; cannot silently produce wrong tip/payroll numbers. Default URL (no `sources`/`breakdown`/`mode`) preserves Composition full-store bars.

## Docs lock-step

- `docs/operator-console/ARCHITECTURE.md` — Sales row + §12 Composition/Trend + cookies
- `docs/operator-console/EXECUTION.md` — §5.5c + Grafana parity Sales row
- `python3 scripts/check_doc_freshness.py`
- `PROGRESS.md` via follow-up retro PR only (never direct main)

## Branch / PR mechanics

- One branch `fix/i198-improving-adding-filters-on-the-sales` → PR #199 `--base main`
- All GitHub ops as `jarvis-agent-bot328`; never self-merge; babysit after push
- Refs #198; cost ledger already bound

## Model routing

- M1–M3: Sonnet (feature work)
- Plan review / hard bugs only: Opus
- Doc polish: Composer/Sonnet

---

## Milestone 1 — Shared time-filter persistence (done)

Cookies `oc_grain` / `oc_from` / `oc_to`; `resolvePageGrain`; FilterSelect + DateRangePicker writers; wire Sales/Labor/Forecast/Order quality/Accounting.

**Verify:** `npm test -- period-grain` — URL > cookie > default.

## Milestone 2 — Sales Composition / Trend + Compare (done)

`chart-mode.ts`, `priorWindow`, `mergePriorSeries`, Sales page mode pills + gated View/Compare.

**Verify:** `npm test -- sales-chart-mode sales-sources`.

## Milestone 3 — Docs + evidence (in progress)

Update ARCHITECTURE/EXECUTION + this plan. Capture E9–E10 screenshots. Refresh PR §4. `verify.py --full`.

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
