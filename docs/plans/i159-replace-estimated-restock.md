# Replace estimated restock date (Operator Console)

Evidence tier: sandbox-e2e

## Jam / §4 (approved)

- Scope: Operator Console only — no Slack changes.
- Action: Replace estimated date (from → to); Estimated-only; then refreshOrderReco.
- Feature flag: no new flag — explicit submit behind existing FEATURES.writeRestock.
- Model routing: Sonnet for implement/UI/evidence; Composer for docs milestone.

### Per-scenario evidence (PR §4)

1. **Happy path:** Replace 2026-07-23 → 2026-07-25; Inventory shows 7/25 Estimated; Order tubs change.
2. **Failure — Actuals:** Replace against a date with actuals is rejected (error; schedule unchanged).
3. **Failure — same date / missing:** from===to or unknown fromDate rejected.
4. **Idempotency / recovery:** 7/16 Actuals untouched; BQ schedule converges after successful replace.
5. Vitest + `python3 scripts/verify.py --full` green.

## Citations

- writes.ts line 12 (`setRestockSchedule`), line 96 (`RestockAction`), line 104 (`submitRestock`)
- queries.ts line 505 (`nextDates`); RestockImportDrawer line 27 (`ACTION_LABELS`)
- inventory/actions.ts line 8; inventory/page.tsx lines 33-39
- `docs/operator-console/ARCHITECTURE.md` line 236 (restock actions); `docs/operator-console/EXECUTION.md` line 257
- Docs lock-step: ARCHITECTURE.md + EXECUTION.md; run `python3 scripts/check_doc_freshness.py`. No RUNBOOK.md (Slack unchanged). CONTRIBUTING.md §4 applies.

## Stubs

```ts
export async function clearRestockSchedule(store: string, deliveryDate: string): Promise<void>
export async function replaceEstimatedRestockDate(
  store: string, fromDate: string, toDate: string, by: string,
): Promise<void>
export function estimatedScheduleDates(store: string): Promise<{ delivery_date: string }[]>
export async function replaceEstimatedRestockDateAction(fromDate: string, toDate: string): Promise<void>
```

```bash
cd apps/operator-console && npx vitest run __tests__/restock-replace.test.ts
cd apps/operator-console && npx vitest run __tests__/restock-import-drawer.test.tsx
python3 scripts/verify.py --full
```

## Invariants

- Idempotent schedule DELETE + MERGE; no orphan actuals.
- America/Chicago for future-date filter (match next-dates view).
- Actuals dates must not be replaceable; 7/16 untouched.
- Dual-date reco refresh after replace; Slack path preserved (no divergence on shared actions).

## Milestone 1 — Write path (Sonnet)

Add clearRestockSchedule + replaceEstimatedRestockDate in writes.ts; estimatedScheduleDates in queries.ts; replaceEstimatedRestockDateAction in actions.ts.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/restock-replace.test.ts
```

## Milestone 2 — UI (Sonnet)

Wire Replace estimated date in RestockImportDrawer + inventory page.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/restock-import-drawer.test.tsx
```

## Milestone 3 — Docs + verify (Composer)

Update ARCHITECTURE.md §5/§6 and EXECUTION.md M3; check_doc_freshness.

**Verify:**
```bash
python3 scripts/verify.py --full
```

## Milestone 4 — §4 live UX (Sonnet)

Local next + Playwright: before/after 7/23→7/25 screenshots to evidence-screenshots release; BQ assert; PR with Closes #159; babysit; never self-merge; operator squash-merges. `gh pr create --base main`.

**Verify:**
```bash
python3 scripts/check_evidence_readiness.py --pr N
```

## Branch / PR mechanics

- Branch: `fix/i159-update-restock-options-on-https-operator`
- One branch = one PR; bot account push; babysit; never self-merge; operator merges.
- Cost: bind-pr + pr_cost_ledger sync.
