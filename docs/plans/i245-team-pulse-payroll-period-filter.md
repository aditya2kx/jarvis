# Team Pulse: payroll period filter for manual Preview/Post (Issue #245)

Evidence tier: sandbox-e2e

## Jam / §4 (approved in chat 2026-08-11)

Operator locked: **manual Preview / Post once only**; Payroll-aligned period list
(`listPayPeriodsWithPaidStatus`); `?period=` + `FilterSelect` in page header;
once-per-CT-day Post once idempotency unchanged. Scheduled webhook / `team_pulse.py`
stays open-period only. Recognition bonuses out of scope.

### Per-scenario evidence (PR §4)

| # | Scenario | Pass criterion |
|---|---|---|
| E1 | Period filter present | `/automations/team-pulse` shows Period `FilterSelect` (Payroll-style labels); default = current unpaid/open. Hosted screenshot `i245-period-default` |
| E2 | Preview closed period | Select non-current period → Preview leaderboard matches that period’s `model_review_bonus_period` totals. Screenshot `i245-preview-closed-period` |
| E3 | Post once to self | Destination = DM → Post once with selected period → ClickUp DM has that leaderboard; history `trigger=once` |
| E4 | Empty period | Period with no credited bonuses → empty-leaderboard copy; no crash / wrong open period |
| E5 | Scheduled unchanged | `agents/bhaga/scripts/team_pulse.py` `fetch_open_leaderboard` still `is_open = TRUE` only (no period CLI); unit/regression notes no Python change |
| E6 | Regression | Existing compose + post-once tests green; new tests for period-scoped query + actions accepting `period_start` |
| E7 | Polish | Filter in `PageHeader` right slot; `min-h-11` / focus ring / mobile tap OK |

Capture:

```bash
python3 apps/operator-console/scripts/capture_evidence.py \
  --path '/automations/team-pulse' --label i245-period-default
python3 apps/operator-console/scripts/capture_evidence.py \
  --path '/automations/team-pulse?period=<closed-start>' --label i245-preview-closed-period
```

Feature flag: **none** — additive manual-path UI; cannot silently produce wrong payroll
numbers (ClickUp compose only). Scheduled path unchanged.

Model routing: Sonnet for all milestones. One chat per PR. Branch
`fix/i-want-to-work-on-a-3` → `gh pr create --base main` → Refs #245 → babysit → never self-merge.

UX polish (`docs/contributing/ui-polish.md`): reuse Payroll’s `FilterSelect` +
`PageHeader` `right` slot; muted freshness line + `Badge` for selected period
(same patterns as `TeamPulseEditor.tsx:197-215`).

## Invariants preserved

- Scheduled / webhook Team pulse still open-period only (`is_open = TRUE`).
- Post once: still ≤1 non-dry-run per America/Chicago calendar day (soft BQ gate).
- Leaderboard dollars/names verbatim; Gemini vary still cannot invent amounts.
- Integer cents / payroll sheets untouched — display + ClickUp message path only.
- Idempotent upserts N/A for this change; no new BQ writes schema.

## Docs lock-step

| Change | Doc |
|---|---|
| Manual period filter on Team pulse | `RUNBOOK.md` Team pulse § (~206–217) |
| Console Automations row | `docs/operator-console/ARCHITECTURE.md` (~171) |
| Checker | `python3 scripts/check_doc_freshness.py --base origin/main` |
| Notable ship | `PROGRESS.md` via post-merge retro follow-up (not direct main) |

## Branch / PR mechanics

- Branch: `fix/i-want-to-work-on-a-3` (Issue #245).
- `gh pr create --base main` as `jarvis-agent-bot328`; never self-merge; babysit.
- Cost: `pr_cost_ledger.py bind-pr` + `sync` after PR exists.

---

## Milestone 1 — Period-scoped BQ leaderboard (Sonnet)

### Files

| Path | Change |
|---|---|
| `apps/operator-console/lib/bq/queries.ts:2392-2439` | Keep `openReviewBonusLeaderboard()` / `openReviewBonusMeta()` for default/open. Add period-scoped helpers (signatures below). |
| `apps/operator-console/__tests__/review-bonus-period-query.test.ts` (new) | Unit-test SQL param binding via mocked `q` **or** pure helper that builds the WHERE clause — assert `period_start` filter, **no** `is_open = TRUE` when period is explicit. |
| `apps/operator-console/lib/automations/teamPulse.ts:51-52` | Empty copy → `_No review bonuses credited in this pay period yet._` (period-agnostic). |

### New signatures (`queries.ts`)

```typescript
/** Review-bonus rows for an explicit biweek (manual Team pulse Preview/Post). */
export async function reviewBonusLeaderboardForPeriod(
  periodStart: string,
): Promise<ReviewBonusLeaderboardRow[]> {
  return q<ReviewBonusLeaderboardRow>(
    `SELECT m.employee, m.total_bonus,
       CAST(m.period_start AS STRING) AS period_start,
       CAST(m.period_end AS STRING) AS period_end
     FROM ${fq("model_review_bonus_period")} m
     WHERE m.period_start = @start
     ORDER BY m.total_bonus DESC, m.employee`,
    { start: dateParam(periodStart) },
  );
}

export async function reviewBonusMetaForPeriod(
  periodStart: string,
): Promise<ReviewBonusOpenMeta | null> {
  const rows = await q<ReviewBonusOpenMeta>(
    `SELECT CAST(m.period_start AS STRING) AS period_start,
       CAST(m.period_end AS STRING) AS period_end,
       CAST(MAX(m.materialized_at_utc) AS STRING) AS materialized_at_utc
     FROM ${fq("model_review_bonus_period")} m
     WHERE m.period_start = @start
     GROUP BY m.period_start, m.period_end`,
    { start: dateParam(periodStart) },
  );
  return rows[0] ?? null;
}
```

`openReviewBonusLeaderboard` remains the open-period path (no callers broken for
schedule parity docs). Console manual actions switch to `reviewBonusLeaderboardForPeriod`.

**Verify:**

```bash
cd apps/operator-console && npx vitest run __tests__/team-pulse-compose.test.ts __tests__/review-bonus-period-query.test.ts
```

Pass: empty-copy string updated; period helper tested; compose tests green.

---

## Milestone 2 — Wire FilterSelect + Preview/Post (Sonnet)

### Files

| Path | Change |
|---|---|
| `apps/operator-console/app/automations/team-pulse/page.tsx:18-68` | Accept `searchParams.period`; load `listPayPeriodsWithPaidStatus(6)`; duplicate Payroll `parsePeriodStart` (lines 34–45 of `payroll/page.tsx` — **do not** refactor payroll); pass `selectedPeriodStart`, `periodOptions`, period-scoped `reviewMeta` into editor; put `FilterSelect` in `PageHeader` `right` (mirror `payroll/page.tsx:210-217`, `basePath="/automations/team-pulse"`). |
| `apps/operator-console/app/automations/team-pulse/TeamPulseEditor.tsx:35-42,138-154,197-215` | Props: `selectedPeriodStart: string`, `periodEnd: string \| null`. Pass `periodStart` into Preview/Post actions. Freshness line shows **selected** period (not only open). Clear `preview` when period prop changes (optional `useEffect`). |
| `apps/operator-console/app/automations/actions.ts:76-152` | `previewTeamPulseAction(periodStart: string)` and `postTeamPulseOnceAction(periodStart: string)` call `reviewBonusLeaderboardForPeriod(periodStart)` instead of `openReviewBonusLeaderboard()`. Validate `periodStart` is non-empty ISO date (`^\d{4}-\d{2}-\d{2}$`); reject otherwise. Once-per-day gate **unchanged**. |
| `apps/operator-console/__tests__/post-team-pulse-once.test.ts` | Mock `reviewBonusLeaderboardForPeriod`; assert action passes `period_start` through; keep once-gate tests. |
| `apps/operator-console/__tests__/preview-team-pulse-period.test.ts` (new) | Preview with explicit period calls period-scoped query, not open-only. |

### Action stubs

```typescript
export async function previewTeamPulseAction(
  periodStart: string,
): Promise<ActionAck<{ content: string; varied: boolean }>> {
  return asAck(async () => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(periodStart)) {
      throw new Error("Invalid pay period.");
    }
    const cfg = await getAutomation(DEFAULT_STORE, AUTOMATION_ID);
    const template = cfg?.template || DEFAULT_TEMPLATE;
    const rows = await reviewBonusLeaderboardForPeriod(periodStart);
    const leaderboard = formatLeaderboard(rows);
    const base = composeMessage(template, leaderboard);
    const { text, varied } = await varyMotivationalCopy(base, leaderboard);
    return { content: text, varied };
  }, "Preview ready.");
}

// postTeamPulseOnceAction(periodStart: string) — same leaderboard swap;
// hasAutomationPostToday / insertAutomationPost / ClickUp path unchanged.
```

**Verify:**

```bash
cd apps/operator-console && npx vitest run \
  __tests__/post-team-pulse-once.test.ts \
  __tests__/preview-team-pulse-period.test.ts \
  __tests__/team-pulse-compose.test.ts \
  __tests__/review-bonus-period-query.test.ts
```

Pass: Preview/Post use selected period; once-gate still blocks second post; invalid period errors.

---

## Milestone 3 — Docs + verify + evidence capture (Sonnet)

### Files

| Path | Change |
|---|---|
| `RUNBOOK.md:206-217` | Note: console Preview/Post once accept a Payroll period filter; scheduler still open period. |
| `docs/operator-console/ARCHITECTURE.md:171` | Automations row: Preview/Post once compose from **selected** `?period=` rollup; schedule remains open. |

**Verify:**

```bash
python3 scripts/check_doc_freshness.py --base origin/main
python3 scripts/verify.py --full
python3 apps/operator-console/scripts/capture_evidence.py \
  --path '/automations/team-pulse' --label i245-period-default
python3 apps/operator-console/scripts/capture_evidence.py \
  --path '/automations/team-pulse?period=<closed-start>' --label i245-preview-closed-period
```

Pass: `verify.py --full` green; hosted https screenshot URLs in PR §4 for E1/E2; E5 noted as no Python/webhook diff.

---

## Out of scope

- Webhook / Cloud Scheduler / `agents/bhaga/scripts/team_pulse.py` period args
- Recognition bonuses in the leaderboard
- Changing once-per-day Post once keying (still CT calendar day, not period)
- Refactoring Payroll to share `parsePeriodStart` (duplicate locally to keep blast radius small)
