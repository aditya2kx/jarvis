# Hotfix: /sales DataTable generic (Issue #203)

Evidence tier: sandbox-e2e

## Jam / §4
Unblock `npm run build` / Cloud Run after #201. Root cause: `DataTable` inferred `TData` from aggregate `tableRows` while `columns` were `ColumnDef<SalesBySourceRow>` (`SalesBySourceRow` has `[key: string]: unknown` index signature).

**Fix:** `DataTable<SalesBySourceRow>` at both sites in `app/sales/page.tsx` lines 361 and 392.

Feature flag: none (type-only). Model routing: Sonnet.

### Per-scenario evidence
1. Happy path: `cd apps/operator-console && npm run build` exits 0.
2. Failure recovery: prior deploy log showed TS error at line 361 — gone after fix.
3. Legacy: icon-rail from #201 unchanged; deploy workflow succeeds on main.

## Citations
- `apps/operator-console/app/sales/page.tsx` lines 361, 392 (`DataTable`)
- `apps/operator-console/lib/bq/queries.ts` lines 86–94 (`SalesBySourceRow`)
- Docs: none required beyond PR (type-only). Run `python3 scripts/check_doc_freshness.py`. CONTRIBUTING.md §4.

## Stubs
```tsx
<DataTable<SalesBySourceRow> columns={columns} data={tableRows} />
```
```bash
cd apps/operator-console && npm run build
python3 scripts/verify.py --full
gh pr create --base main …
```

## Invariants
Must not break Sales UI behavior; type-only. No money/cents changes. Idempotent.

## Milestone 1 — Type fix (Sonnet)
**Verify:** `cd apps/operator-console && npm run build`

## Milestone 2 — Verify (Sonnet)
**Verify:** `python3 scripts/verify.py --full`

## Milestone 3 — Ship + deploy (Sonnet)
PR mechanics: one branch, `gh pr create --base main`, never self-merge, babysit. Post-merge: operator-console-deploy green.

**Verify:** `gh run list --workflow=operator-console-deploy.yml --limit 1`
