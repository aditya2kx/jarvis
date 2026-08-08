# Issue #227 — Hour-of-day Aggregation + Labor % of net sales toggle

Evidence tier: sandbox-e2e
scenario: sales-hour-labor-pct-toggle

## Jam / §4 (approved 2026-08-07)

- **Sales Aggregation:** add `hour` grain (like `weekday`) — collapse Period onto 24 local-hour buckets; net sales / orders / items by hour.
- **Labor chart toggle:** `FilterPills` Hours | % of net sales on Labor hours chart; % mode = completed actuals only; PT/FT (+ total) kept; no schedule stacks; weekly hours goal hours-mode only.
- Feature-flag decision: **no new flag** — additive UI; cannot silently produce wrong tip/payroll numbers; BQ ratios already recomputed in `laborByGrain`.
- Model routing: Sonnet implement; Composer docs.
- Branch: `fix/i-want-to-work-on-a`; `gh pr create --base main`; never self-merge; babysit; operator squash-merge.
- Closes #227.

### Per-scenario evidence (PR §4) — hosted screenshots required

1. **Happy — Sales Hour:** `/sales?grain=hour` — 24-bucket spine / open hours; bars = net sales by local hour.
2. **Happy — Weekday regression:** `/sales?grain=weekday` still Mon…Sun.
3. **Happy — Labor Hours (default):** hours stacks + schedule when Period includes today; tooltip `(X%)` intact.
4. **Happy — Labor % mode:** `/labor?unit=pct` — Y-axis %; PT/FT stack; no schedule slate; incomplete buckets empty.
5. **Happy — Labor type scope:** `labor_type=Part-time` in % mode uses `hourly_pct`.
6. **Failure — zero sales day:** null/`—`, not NaN/Infinity.
7. **Legacy:** other grains + Labor concurrent/coverage unchanged.
8. Unit tests + `python3 scripts/verify.py --full` green.
9. Post-merge: prod `/sales?grain=hour` + `/labor?unit=pct` smoke vs BQ.

## Citations

- `apps/operator-console/lib/filters/range.ts` L224–260 (`Grain`, `GRAINS`, `parseGrain`); L359–438 (`enumerateBucketStarts`, `bucketSql`); L485–531 (`formatBucket`, `truncateToGrain`)
- `apps/operator-console/lib/bq/queries.ts` L350–423 (`salesByGrain`); L53–79 (`laborByGrain` — already returns `labor_pct` / `hourly_pct` / `fulltime_pct`)
- `apps/operator-console/components/filters/AggregationSelect.tsx` L9–28
- `apps/operator-console/app/sales/page.tsx` L45–120 (grain + charts); L208–258 (FilterPills pattern)
- `apps/operator-console/components/labor/LaborHoursChart.tsx` L72–100 (`formatHoursWithPct`, `scopedLaborMetrics`); L236–338 (chart)
- `apps/operator-console/app/labor/page.tsx` L72–100 (searchParams); L370–375 (`LaborHoursChart`)
- `apps/operator-console/components/filters/FilterPills.tsx` L19–58
- `apps/operator-console/components/charts/BarChartCard.tsx` L20 (`BarValueFormat` includes `percent`)
- `core/migrations/002_views.sql` L29–43 (`vw_tips_by_hour` / `hour_local`)
- Docs lock-step: `docs/operator-console/ARCHITECTURE.md` § Grain; Labor note; `python3 scripts/check_doc_freshness.py`. No RUNBOOK.md (console-only). CONTRIBUTING.md §4.

## Stubs

```ts
// range.ts
export type Grain = "day" | "week" | "month" | "weekday" | "hour" | "all";
export const HOUR_ANCHOR_START = "1970-01-01"; // hour 0 → … hour 23 = 1970-01-24
export const GRAINS_WITHOUT_HOUR = GRAINS.filter((g) => g.value !== "hour");
export function hourBucketSql(hourCol = "hour_local"): string {
  return `DATE_ADD(DATE '${HOUR_ANCHOR_START}', INTERVAL ${hourCol} DAY)`;
}
export function hourIndexFromAnchor(isoDate: string): number; // 0..23
```

```ts
// lib/filters/labor-chart-unit.ts
export type LaborChartUnit = "hours" | "pct";
export const LABOR_CHART_UNIT_OPTIONS = [
  { value: "hours", label: "Hours" },
  { value: "pct", label: "% of net sales" },
];
export function parseLaborChartUnit(v: string | string[] | undefined): LaborChartUnit;
```

```ts
// AggregationSelect — optional options prop (default GRAINS_WITHOUT_HOUR;
// Sales + Labor pass GRAINS)
// salesByGrain — if grain === "hour", group by hour_local + hourBucketSql; else existing path
// LaborHoursChart — unit prop; pct → valueFormat=percent, bars = pct*100, no sched/goal
```

```bash
cd apps/operator-console && npx vitest run __tests__/grain.test.ts __tests__/labor-hours-chart.test.ts
cd apps/operator-console && npm run dev
# verify:
#   http://localhost:3000/sales?grain=hour
#   http://localhost:3000/labor?unit=pct
python3 scripts/verify.py --full
```

## Invariants

- America/Chicago already baked into `hour_local` / `date_local` — do not re-timezone.
- `grain` never string-interpolated into SQL — closed `Grain` union → whitelisted SQL only.
- Labor % = SAFE_DIVIDE(labor_cost, net_sales) already in BQ; UI must not invent ratios.
- Hour grain is Sales-only in Aggregation UI; other pages coerce `hour` → `day` if cookie/URL leaks.
- Idempotent read-only BQ; no pipeline/sheet writes; sandbox isolation N/A (console ADC dogfood).

## Milestone 1 — Grain plumbing + salesByGrain hour (Sonnet)

Extend `Grain`/`GRAINS`/`bucketSql`/`formatBucket`/`enumerateBucketStarts`/`truncateToGrain`/`addGrain`/`priorWindow` for `hour`. `hourBucketSql`; Sales AggregationSelect shows Hour; Labor/OQ/Accounting use `GRAINS_WITHOUT_HOUR` + coerce. Special-case `salesByGrain` for hour via `hour_local`.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/grain.test.ts
```

## Milestone 2 — Labor unit toggle (Sonnet)

Add `labor-chart-unit.ts`; Labor page FilterPills + pass `unit` into `LaborHoursChart`; % mode uses `hourly_pct`/`fulltime_pct`/`labor_pct` ×100, strips schedule + goal; update tooltips; vitest.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/labor-hours-chart.test.ts
```

## Milestone 3 — Docs + localhost + verify (Sonnet)

ARCHITECTURE.md grain list + Labor unit; `check_doc_freshness.py`; localhost dogfood; `verify.py --full`; PR §4 screenshots via `capture_evidence.py`.

**Verify:**
```bash
python3 scripts/check_doc_freshness.py --base origin/main
python3 scripts/verify.py --full
```
