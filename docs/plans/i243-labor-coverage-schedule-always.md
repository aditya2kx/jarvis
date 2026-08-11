---
name: i243 labor coverage schedule always
overview: "Staffing coverage always shows ADP schedule when rows exist; Aggregation must not gate coverage. Charts keep hour-grain schedule hide. Localhost before PR."
todos:
  - id: m1-gates
    content: "M1: schedule-fetch-gates helper + labor page split fetch"
    status: pending
  - id: m2-docs
    content: "M2: ARCHITECTURE + coverage copy lock-step"
    status: pending
  - id: m3-localhost
    content: "M3: localhost /labor?grain=hour demo before PR"
    status: pending
isProject: false
---

# Labor staffing coverage always shows schedule (Issue #243)

Derived from jam + §4 approved 2026-08-11. Branch `fix/want-to-work-on-an-improvement`.
Scope **A+B** (decouple Aggregation + show coverage schedule for future-only Periods when `scheduledShiftWindow` non-null).

**Evidence tier: sandbox-e2e**

## Feature-flag decision

No new FEATURE_FLAGS.md entry / no runtime flag. Change is fetch/UI gating only on Operator Console Labor page — cannot silently produce wrong tip or payroll cents (read-only BQ schedule rows; Hours/Concurrent hour-grain stacks unchanged per #227).

## Invariants preserved

- Idempotent BQ reads only (no writes).
- Integer cents / tip math untouched.
- America/Chicago windows via `scheduledShiftWindow` / SQL `CURRENT_DATE('America/Chicago')`.
- PTO + labor_type filters still apply to coverage.
- Sandbox isolation N/A (console UI).

## Docs lock-step

Update in same change:
- [`docs/operator-console/ARCHITECTURE.md`](docs/operator-console/ARCHITECTURE.md) — Labor L-section: Staffing coverage schedule independent of Aggregation.
- Labor page help blurb + `LaborCoveragePanel` CardDescription.
- Run `python3 scripts/check_doc_freshness.py` before verify.

## Branch / PR mechanics

One branch = one coherent change; `gh pr create --base main`; bot account; never self-merge; reply-to-every-comment; babysit to green. Operator plays **localhost before PR**.

## Model routing

- Milestone 1–2: Sonnet 5 medium thinking (implement)
- Milestone 3: Composer/Sonnet for demo notes; Opus only if review blocks

### Per-scenario evidence (PR §4) — hosted screenshots

1. Happy — `/labor?grain=hour` coverage shows schedule; Hours has no schedule stacks.
2. Happy — `/labor?grain=day` regression.
3. Happy — `labor_type=Part-time` + `pto=exclude`.
4. Legacy — Hours/Concurrent hour grain no schedule stacks.
5. Failure — empty schedule no crash.
6. `vitest` + `python3 scripts/verify.py --full`.
7. Post-merge prod `/labor?grain=hour` smoke.

---

## Milestone 1 — Gates helper + page split

**Model: Sonnet 5 medium thinking**

### Changes

1. New [`apps/operator-console/lib/labor/schedule-fetch-gates.ts`](apps/operator-console/lib/labor/schedule-fetch-gates.ts) — export:

```ts
export function showChartSchedule(opts: {
  includesToday: boolean;
  hasSchedWin: boolean;
  grain: string;
}): boolean {
  return opts.includesToday && opts.hasSchedWin && opts.grain !== "hour";
}

export function showCoverageSchedule(opts: { hasSchedWin: boolean }): boolean {
  return opts.hasSchedWin;
}
```

2. [`apps/operator-console/app/labor/page.tsx:151`](apps/operator-console/app/labor/page.tsx) — replace `showSchedule` with split gates; fetch `laborScheduledHoursByGrain` only when `showChartSchedule`; fetch `laborScheduledShiftDays` when chart **or** coverage needs rows; assign `coverageScheduled` from coverage gate; `rollConcurrentToGrain` only when chart schedule on ([`page.tsx:183`](apps/operator-console/app/labor/page.tsx)).

3. Tests: [`apps/operator-console/__tests__/labor-schedule-gates.test.ts`](apps/operator-console/__tests__/labor-schedule-gates.test.ts) — hour grain → chart false / coverage true; past-only `hasSchedWin=false` → both false; future-only includesToday false + hasSchedWin true → coverage true, chart false.

**Verify:** `cd apps/operator-console && npx vitest run __tests__/labor-schedule-gates.test.ts`  
**Pass:** all gate cases green.

---

## Milestone 2 — Docs + copy

**Model: Sonnet 5 medium thinking**

### Changes

1. [`apps/operator-console/app/labor/page.tsx:455`](apps/operator-console/app/labor/page.tsx) — help text: Staffing coverage shows schedule whenever ADP schedule exists in the schedule window, independent of Aggregation (Hour included).
2. [`apps/operator-console/components/labor/LaborCoveragePanel.tsx:514`](apps/operator-console/components/labor/LaborCoveragePanel.tsx) — CardDescription same intent.
3. [`docs/operator-console/ARCHITECTURE.md:503`](docs/operator-console/ARCHITECTURE.md) — note L3/coverage: schedule swimlanes not gated by Aggregation; charts omit schedule stacks on Hour.

**Verify:** `python3 scripts/check_doc_freshness.py`  
**Pass:** exit 0 for touched docs.

---

## Milestone 3 — Localhost demo + verify

**Model: Sonnet 5 medium thinking**

### Changes

1. `cd apps/operator-console && npm run dev` — open `/labor?grain=hour`; confirm Staffing coverage slate schedule.
2. Run full verify before PR (after operator OK on localhost).

**Verify:** `python3 scripts/verify.py --full`  
**Pass:** green; operator ACK on localhost before `gh pr create`.
