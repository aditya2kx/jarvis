# #213 Labor page simplify + strip Forecast + dual-source wage rates

Evidence tier: sandbox-e2e
scenario: ADP pay_info gap-fill (Brooke/Elizabeth class) + Operator Console Labor/Forecast strip
waiver: none — console unit + hosted evidence screenshots; ADP pay_info live scrape for gap employees

## Jam / §4 (approved 2026-08-01)

- **D1:** Remove `/forecast` nav + page; no forecast-model numbers in Operator Console.
- **D2 (assumed):** Keep BQ/Grafana forecast pipeline.
- **D3:** Scheduled shifts UI deferred to follow-up.
- **D4:** Drop Labor forward / Wage-Paid-Blended lenses / forecast-tied projections.
- **L1:** One historical ADP hours bar — Period + grain; Aggregate | PT/FT; Hours | % of Square net sales (= labor $ / net sales).
- **L2:** Drop hrs/item, throughput, daily detail table.
- **L3:** Hours-per-person → bar chart for page Period (not open-pay-period-only).
- **Wage:** Dual-source — keep Earnings Regular; add People → Payroll info gap-fill; backfill gaps; end-of-run assert.

## Citations

- `apps/operator-console/app/labor/page.tsx` — full rewrite of chart sections (L1–L3, strip D1/D4)
- `apps/operator-console/app/forecast/page.tsx` — delete (or 404 redirect)
- `apps/operator-console/components/shell/nav-items.ts:39` — remove Forecast nav item + `TrendingUp`
- `apps/operator-console/lib/config/features.ts:6` — `forecast: false`
- `apps/operator-console/lib/bq/queries.ts:53` `laborByGrain` — keep; add `laborHoursPerPerson(win)`
- `apps/operator-console/components/accounting/AccountingLedger.tsx:74,300-323` — copy ChartUnit toggle pattern
- `apps/operator-console/app/sales/page.tsx:240-257` — copy Aggregate/Breakdown `FilterPills`
- `apps/operator-console/lib/filters/sources.ts:42` `parseBreakdown`
- `skills/adp_run_automation/compensation_backend.py:261` `infer_wage_rates`
- `skills/adp_run_automation/runner.py:1861` `download_adp_bundle`
- `agents/bhaga/scripts/backfill_from_downloads.py:343-390` adp_rates load
- `agents/bhaga/scripts/backfill_bigquery.py:213` `map_adp_wage_rate`
- `core/migrations/001_initial_schema.sql:73-84` adp_wage_rates; new `050_wage_rate_source.sql`
- `agents/bhaga/scripts/daily_refresh.py:295` `_should_run_rates`
- Branch: `fix/i213-want-to-work-on-update-labor`; bot `jarvis-agent-bot328`; PR `--base main`; never self-merge

### Artifacts

```sql
-- core/migrations/050_wage_rate_source.sql
ALTER TABLE `jarvis-bhaga-prod.bhaga.adp_wage_rates`
  ADD COLUMN IF NOT EXISTS rate_source STRING;
-- values: earnings | pay_info | roster_stub
```

```python
# skills/adp_run_automation/pay_info_backend.py
def scrape_pay_info_rates(
    page, names: list[str], *, store: str = "palmetto"
) -> list[dict]:
    """People → Payroll info → Hourly pay rate for each name. rate_source=pay_info."""

def gap_employees_from_bq(*, days: int = 60, store: str = "palmetto") -> list[str]:
    """Punchers in window missing non-null wage_rate_dollars."""

def write_pay_info_rates(rates: list[dict], *, dry_run: bool = False) -> int: ...
```

```ts
// apps/operator-console/components/labor/LaborHoursChart.tsx
export type LaborChartUnit = "hours" | "pct_net_sales";
export function LaborHoursChart(props: {
  data: Array<{ date: string; total_hours: number | null; parttime_hours: number | null;
    fulltime_hours: number | null; labor_pct: number | null; hourly_pct: number | null;
    fulltime_pct: number | null }>;
  breakdown: boolean; // Aggregate vs PT/FT
  grain: Grain;
}): JSX.Element
```

```bash
# Backfill gaps then rematerialize labor $
python3 -m skills.adp_run_automation.pay_info_backend --from-bq-gaps --days 60 --write-bq
# after rates land:
python3 -m agents.bhaga.scripts.materialize_model_bq --store palmetto  # or daily_refresh model step
```

### Per-scenario evidence (PR §4)

1. **E1 Console Forecast gone:** nav has no Forecast; `/forecast` 404 or redirect; FEATURES.forecast false.
2. **E2 Labor L1 Aggregate hours:** `/labor?range=30d&grain=day&breakdown=0` — one hours bar; hosted screenshot.
3. **E3 Labor L1 PT/FT breakdown:** `breakdown=1` stacked PT/FT; screenshot.
4. **E4 Labor L1 % mode:** client toggle → labor % of Square net sales (labor$/net_sales); Aggregate + breakdown.
5. **E5 Labor L3:** hours-per-person bar for Period (not open-pay-period list); screenshot.
6. **E6 Labor stripped:** no lens pills, forward card, hrs/item, throughput, daily detail, forecast numbers.
7. **E7 Unit:** Labor chart helpers + pay_info parse tests; `cd apps/operator-console && npm test`; `verify.py --full`.
8. **E8 Wage backfill:** Brooke + Elizabeth have non-null `adp_wage_rates.wage_rate_dollars` with `rate_source=pay_info` (BQ query evidence).
9. **E9 Incremental gap-fill:** after timecard (same ADP session), missing punchers scrape pay_info; earnings Regular overwrites; gap-fill runs whenever gaps exist (not Mon/Tue-only).
10. **E10 End-of-run:** assert/breadcrumb if any Active puncher still missing rate.
11. **E11 Failure/recovery:** per-employee scrape failure leaves breadcrumb + continues; does not wipe earnings rates.
12. **E12 Docs:** DOMAIN/RUNBOOK/adp README + operator-console ARCHITECTURE/EXECUTION; `check_doc_freshness.py`.

```bash
python3 scripts/check_plan_readiness.py --plan docs/plans/i213-labor-forecast-wage-rates.md
cd apps/operator-console && npm test -- --run
python3 scripts/verify.py --full
python3 apps/operator-console/scripts/capture_evidence.py --path '/labor?range=30d&grain=day&breakdown=0' --label labor-l1-agg
python3 apps/operator-console/scripts/capture_evidence.py --path '/labor?range=30d&grain=day&breakdown=1' --label labor-l1-ptft
# BQ after backfill:
# SELECT canonical_name, wage_rate_dollars, rate_source FROM adp_wage_rates
# WHERE canonical_name LIKE '%Brooke%' OR canonical_name LIKE '%Elizabeth%'
```

## Milestones

### M1 — Operator Console Labor/Forecast (Composer) — independently verifiable
- Delete Forecast nav + page; `forecast: false`.
- Rewrite Labor page: L1 chart component + L3 hours-per-person query; strip L2/D4.
- Tests for parseBreakdown wiring + chart unit remap.
- Pass: `npm test` + `npm run build`; local `/labor` shows new layout.

### M2 — Schema + pay_info scrape + load (Sonnet) — independently verifiable
- Migration `050_wage_rate_source.sql`; extend `map_adp_wage_rate`.
- `pay_info_backend.py` + selectors; CLI `--from-bq-gaps --write-bq`.
- Hook in `download_adp_bundle` / daily_refresh after earnings load when gaps.
- Pass: unit parse tests; dry-run scrape lists Brooke/Elizabeth rates.

### M3 — Backfill + model rematerialize + evidence (Sonnet)
- Live `--write-bq` for gaps; rematerialize labor daily.
- Hosted Labor screenshots; BQ rate rows evidence in PR §4.
- Pass: E1–E12; `verify.py --full`; babysit PR.

## Invariants

- Idempotent MERGE on `employee_id`; earnings Regular never overwritten by pay_info.
- Integer cents unchanged elsewhere; wage rates remain FLOAT64 dollars (existing).
- America/Chicago date boundaries for console Period.
- Read-only ADP (scrape only); sandbox isolation for console evidence paths.
- No silent wrong labor $: gap-fill only fills NULL; median fallback remains last resort until rate lands.

## Feature-flag decision

**No new FEATURE_FLAGS entry for Labor UI** — display-only strip; cannot silently wrong payroll tip numbers.

**Pay_info scrape:** gate via existing ADP bundle knobs / soft-fail per employee (not a feature flag that can leave wrong rates on). Gap-fill is additive fill of NULL only — cannot silently produce wrong numbers vs median fallback (improves accuracy). Document in DOMAIN/RUNBOOK; no FEATURE_FLAGS.md required under “can it silently produce wrong numbers?” test (fills missing only; earnings wins).

## Docs lock-step

- `docs/operator-console/ARCHITECTURE.md` — Labor + remove Forecast console row
- `docs/operator-console/EXECUTION.md` — Labor IA
- `agents/bhaga/knowledge-base/DOMAIN.md` — dual-source rates
- `skills/adp_run_automation/README.md` — pay_info gap-fill
- `RUNBOOK.md` — wage gap-fill breadcrumb
- `python3 scripts/check_doc_freshness.py`

## Branch / PR

- One branch = one coherent change (console + wage dual-source per operator: same worktree).
- `jarvis-agent-bot328`; `gh pr create --base main`; never self-merge; reply-to-every-comment; babysit skill.

## Model routing

- M1 Composer; M2–M3 Sonnet; jam already used Opus.
