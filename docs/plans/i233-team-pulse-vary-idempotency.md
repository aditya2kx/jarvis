# Team Pulse: multi-vary reject + once-idempotency + freshness (Issue #233)

Evidence tier: unit-only
waiver: Console + TS/Python bugfix (Gemini multi-draft + soft once-gate + pending footgun); no BHAGA pipeline/sheet mutation. Prove with unit tests + hosted Operator Console screenshots (portal G5). No sandbox-live.

## Jam / §4 (approved in chat 2026-08-09)

Operator chose **A** (leaderboard freshness / rollup lag) + multi-draft fix + once-idempotency + freshness chip.
Out of scope: new Automations types, review-bonus pool rule changes, ClickUp destination UX.

### Prod diagnosis (read-only)

- `automation_posts.message_id=80170041046292` (`2026-08-08`, `trigger=once`): **one** ClickUp message whose `content` concatenates **three** Gemini drafts separated by `---`.
- `varyMotivationalCopy` (`apps/operator-console/lib/automations/varyCopy.ts:47-51`) accepts any text that `includes(leaderboard)` — multi-draft passes.
- `2026-08-05`: two `once` rows ~16 min apart (soft `hasAutomationPostToday` + `useConsoleAction` async `startTransition` clears `isPending` mid-flight — `apps/operator-console/lib/actions/useConsoleAction.ts:30-58`).
- Aug 5 posts predated Aug 7 Ximena/Dolce credits; Aug 8 post included them. Open period still `2026-07-27`→`2026-08-08`.

### Per-scenario evidence (PR §4)

| # | Scenario | Pass criterion |
|---|---|---|
| E1 | Happy path — single vary | Unit: one rewrite, verbatim leaderboard → `varied: true`, single greeting block |
| E2 | Multi-draft rejection | Unit: body with `---` / 2–3 drafts / repeated leaderboard → fallback template, never concatenate |
| E3 | Once-per-day | Unit/action guard: second post same CT day errors; ≤1 non-dry-run intent (pre-ClickUp recheck + busy lock) |
| E4 | Freshness UI | Hosted screenshot: open period dates + rollup time on Team Pulse; leaderboard shows current open-period names |
| E5 | Pending / double-submit | Hosted screenshot or note: Post once stays disabled for full flight |
| E6 | Python parity | `test_team_pulse.py`: same multi-draft reject for `vary_motivational_copy` |
| E7 | Regression | `team-pulse-compose` + `verify.py --full` green |

Post-merge (read-only): new `trigger=once` rows have one greeting block (no `---` multi-draft); ≤1 successful operator expectation per CT day under normal UI use.

Feature flag: **none** — always-on bugfix; cannot silently produce wrong payroll numbers (display/post path only). Wrong-numbers risk = ClickUp spam / confusing copy — fixed by reject + pending.

Model routing: Sonnet for all milestones. One chat per PR.

UX polish (`docs/contributing/ui-polish.md`): reuse `Badge`, `Card`, muted text; freshness chip uses existing `Badge` + `text-muted-foreground`; buttons keep `min-h-11` / `disabled={isPending}`.

## Invariants preserved

- Leaderboard dollars/names stay verbatim (never invent amounts).
- Once-per-CT-day soft gate remains; UI busy lock + pre-ClickUp recheck narrow the race (BQ has no enforced UNIQUE on `automation_posts` — `054_automations.sql:25-37`).
- America/Chicago date boundary via `chicagoTodayIso` / existing CT helpers.
- Idempotent: re-Preview OK; second Post once same day still errors.
- Sandbox isolation N/A (no BHAGA write path change beyond Gemini parse + console).

## Docs lock-step

| Change | Doc |
|---|---|
| Team Pulse vary / once behavior | `RUNBOOK.md` (Team pulse / automations section ~206–240) |
| Console Automations behavior | `docs/operator-console/ARCHITECTURE.md` Automations row |
| Notable ship | `PROGRESS.md` via post-merge retro follow-up PR (not direct main) |
| Checker | `python3 scripts/check_doc_freshness.py --base origin/main` |

## Branch / PR mechanics

- Branch: `fix/autoamtions-page-is-not-picking-up` (Issue #233).
- `gh pr create --base main` as `jarvis-agent-bot328`; never self-merge; babysit to green; operator squash-merge.
- Cost: `pr_cost_ledger.py bind-pr` + `sync` after PR exists; do not commit secrets.

---

## Milestone 1 — Gemini single-message accept (Sonnet)

### Files

| Path | Change |
|---|---|
| `apps/operator-console/lib/automations/varyCopy.ts:12-56` | Extract `acceptVariedCopy(text, lb)`; reject if leaderboard count ≠ 1, or `\n---\n` draft separators, or length ≫ base; tighten prompt rule 4 to “exactly one message, no alternatives” |
| `apps/operator-console/__tests__/vary-copy.test.ts` (new) | E1/E2 fixtures (no live Gemini) |
| `agents/bhaga/scripts/team_pulse.py:56-118` | Mirror `accept_varied_copy` in `vary_motivational_copy` |
| `agents/bhaga/scripts/test_team_pulse.py` | Add multi-draft / happy-path accept tests (mock or pure helper) |

### Stub — accept helper (TS)

```typescript
export function acceptVariedCopy(
  text: string,
  leaderboardMd: string,
): { text: string; varied: boolean } {
  const lb = leaderboardMd.trim();
  const t = text.trim();
  if (!t || !lb) return { text: "", varied: false };
  const occurrences = t.split(lb).length - 1;
  if (occurrences !== 1) return { text: "", varied: false };
  if (/(^|\n)\s*---\s*(\n|$)/.test(t)) return { text: "", varied: false };
  return { text: t, varied: true };
}
```

Wire into `varyMotivationalCopy` after Gemini response: if accept fails → original `message`, `varied: false`.

**Verify:**

```bash
cd apps/operator-console && npx vitest run __tests__/vary-copy.test.ts __tests__/team-pulse-compose.test.ts
python3 -m pytest agents/bhaga/scripts/test_team_pulse.py -q
```

Pass: multi-draft fixtures rejected; single-draft accepted; compose tests unchanged.

---

## Milestone 2 — Once-gate + pending lock (Sonnet)

### Files

| Path | Change |
|---|---|
| `apps/operator-console/lib/actions/useConsoleAction.ts:17-65` | Track real in-flight with `useState` busy flag set true before `await fn()` and false in `finally` (do not rely on async `startTransition` for `isPending`) |
| `apps/operator-console/app/automations/actions.ts:102-126` | Re-check `hasAutomationPostToday` immediately before `postChatMessage`; keep existing throw after first check |
| `apps/operator-console/app/automations/team-pulse/TeamPulseEditor.tsx:366-392` | Keep `disabled={isPending}` (now true for full flight) |

No BQ UNIQUE migration — BigQuery does not enforce uniqueness on `automation_posts`; document residual TOCTOU under concurrent non-UI callers in RUNBOOK one-liner.

**Verify:**

```bash
cd apps/operator-console && npx vitest run
# Manual / capture: Post once disabled until toast; second click no-ops while busy
```

Pass: `isPending` stays true across awaited server action; second Post while in flight does not fire.

---

## Milestone 3 — Freshness chip + docs + evidence (Sonnet)

### Files

| Path | Change |
|---|---|
| `apps/operator-console/lib/bq/queries.ts:2341-2367` | Extend `openReviewBonusLeaderboard` / add `openReviewBonusMeta()` returning `period_start`, `period_end`, `MAX(materialized_at_utc)` |
| `apps/operator-console/app/automations/team-pulse/page.tsx:17-65` | Fetch meta + pass to editor |
| `apps/operator-console/app/automations/team-pulse/TeamPulseEditor.tsx` | Props + Badge/muted line: `Open period {start}–{end} · rollup {materialized}` |
| `apps/operator-console/app/automations/page.tsx` | Optional one-line cadence already present — no new automation cards |
| `RUNBOOK.md` | Note: Team Pulse reads open-period BQ rollup (nightly `process_reviews`); Gemini must return one message; once/day soft + UI busy |
| `docs/operator-console/ARCHITECTURE.md` | Automations row: freshness + vary accept |

**Verify:**

```bash
python3 apps/operator-console/scripts/capture_evidence.py --path /automations/team-pulse --label i233-freshness
python3 scripts/check_doc_freshness.py --base origin/main
python3 scripts/verify.py --full
```

Pass: hosted https screenshot shows period + rollup; docs checker clean; verify green.

---

## Implementation order

1. M1 accept helper + tests (TS then Python)
2. M2 useConsoleAction busy + pre-ClickUp recheck
3. M3 freshness query/UI + docs + capture_evidence
4. PR §4 paste E1–E7; babysit

## Residual risk (accepted)

Concurrent non-UI double-post (two Cloud Run / two browsers) can still race BQ check-then-act; UI path is the operator-reported failure mode. Follow-up only if scheduler+once collide in prod logs.
