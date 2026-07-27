# Forecast page: Period does not clip forward widgets (#202)

Evidence tier: sandbox-e2e
(+ mandatory prod-live Operator Console screenshots — portal path; G5)

## Jam / §4 (approved 2026-07-27)

Forward-looking `/forecast` widgets (Upcoming schedule, Forecast vs prior week, Goal hrs vs scheduled) always show Chicago today → pipeline horizon (~30d). Period drives accuracy chart + MAPE only. Exclusions stay fixed 60d. Keep Period control on page. Out of scope: `forecast_horizon_days`, ADP scrape length, Labor forward KPIs.

Feature flag: **none** — display window only; cannot silently produce wrong payroll/tip numbers.

Invariants preserved: America/Chicago today; integer-cents N/A (forecast floats as today); sandbox isolation N/A; read-only BQ.

### Per-scenario evidence (PR §4)

Happy path + failure/recovery covered below (pass criterion: each scenario verified before merge).

1. **E1 Happy path — late-month Period:** Period=This month near month-end → forward table/charts include dates past month-end.
2. **E2 Happy path — past-only Period:** Period=Last month → accuracy scoped; forward widgets still today→horizon (not empty).
3. **E3 Happy path — short Period:** Period=Last 7 days → MAPE/accuracy 7d; forward full horizon.
4. **E4 Regression — exclusions:** still last 60 days; Period-independent.
5. **E5 Grain:** day→week rollup on forward table still works.
6. **E6 Unit:** `forecastForwardByGrain` has no Period `@end`; accuracy still gets `win`.
7. **E7 Docs:** EXECUTION.md §5.5a + ARCHITECTURE grain list updated; `check_doc_freshness.py` clean.
8. **E8 Verify:** `python3 scripts/verify.py --full` green.
9. **E9 Failure/recovery — empty pipeline:** when BQ returns zero forward rows, Upcoming schedule shows the new empty-state (mentions pipeline, not “try This month”); accuracy chart still renders for Period.

## Citations

- `apps/operator-console/lib/bq/queries.ts:450` — `forecastGrainSelectSql` / `forecastByGrain` / `forecastForwardByGrain`
- `apps/operator-console/app/forecast/page.tsx:49` — split forward vs accuracy fetches
- `docs/operator-console/EXECUTION.md:433` — §5.5a Period semantics for Forecast
- `docs/operator-console/ARCHITECTURE.md:408` — grain readers list
- `apps/operator-console/__tests__/forecast-forward.test.ts:1` — unit contract
- Branch: `fix/i202-improvement-for-the-forecast-page`; bot `jarvis-agent-bot328`; PR `--base main`; never self-merge; babysit skill

```ts
export function forecastForwardByGrain(grain: Grain): Promise<ForecastRow[]>
export function forecastByGrain(win: DateWindow, grain: Grain): Promise<ForecastRow[]>
export function forecastAccuracyByGrain(win: DateWindow, grain: Grain): Promise<ForecastAccuracyRow[]>
```

```bash
cd apps/operator-console && npm test -- --run forecast-forward
python3 scripts/check_doc_freshness.py
python3 scripts/verify.py --full --plan docs/plans/i202-forecast-period-clip.md
python3 apps/operator-console/scripts/capture_evidence.py --path '/forecast?range=this_month&grain=day' --label forecast-forward-this-month
python3 apps/operator-console/scripts/capture_evidence.py --path '/forecast?range=last_month&grain=day' --label forecast-forward-last-month
```

Model routing: Sonnet for implement/verify; Composer for docs-only polish.

## Milestone 1 — Forward BQ reader

Add `forecastForwardByGrain(grain)` beside `forecastByGrain` in `queries.ts` (~line 450). Shared SELECT via `forecastGrainSelectSql`; WHERE `date >= CURRENT_DATE('America/Chicago')` (no Period `@end`).

**Verify:**
```bash
cd apps/operator-console && npm test -- --run forecast-forward
```

## Milestone 2 — Wire `/forecast` page

`ForecastPage` calls `forecastForwardByGrain(grain)` for schedule/goal charts + Upcoming schedule; keep `forecastAccuracyByGrain(win, grain)` for accuracy + MAPE. Update empty-state copy (no “try This month”).

**Verify:**
```bash
cd apps/operator-console && npm test -- --run forecast-forward
python3 scripts/check_doc_freshness.py
```

## Milestone 3 — Docs + §4 screenshots

Update EXECUTION §5.5a + ARCHITECTURE grain list. Capture hosted screenshots E1–E4; paste into PR §4. `verify.py --full` green; babysit.

**Verify:**
```bash
python3 scripts/verify.py --full --plan docs/plans/i202-forecast-period-clip.md
python3 apps/operator-console/scripts/capture_evidence.py --path '/forecast?range=last_month&grain=day' --label forecast-forward-last-month
```

Docs lock-step: EXECUTION.md, ARCHITECTURE.md; run `python3 scripts/check_doc_freshness.py`. No RUNBOOK.md / PROGRESS.md / FEATURE_FLAGS (display-only window; PROGRESS via post-merge retro if warranted).
