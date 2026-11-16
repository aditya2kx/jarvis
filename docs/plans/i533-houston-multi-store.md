# Issue #533 — Operator Console for Austin + Houston

Branch `fix/as-i-am-opening-houston-store` · tracking issue #533 · Houston opens 2026-11-23.

Consulted: `CONTRIBUTING.md` (dev loop, evidence tiers), `docs/WORKFLOW.md`, `.cursor/rules/bhaga.mdc`
+ `bhaga-principles.mdc` (invariants), `docs/operator-console/ARCHITECTURE.md` §1/§4/§5/§15,
`docs/contributing/ui-polish.md` (D1–D10), `docs/contributing/sandbox-evidence.md`.

## Agreed scope (jam, 2026-11-16)

- Store switcher (Austin / Houston) plus a combined both-stores view.
- Houston screens: Home, Sales, Labor, Payroll, Inventory, Accounting. Order Quality and
  Automations stay Austin-only.
- Houston: Square location `L6AQ95CN5NG4W` (same Square account), a separate ADP company on the
  same login, a separate team.

## Spike results that fix the design

| Spike | Finding | Consequence |
|---|---|---|
| Square | Existing OAuth token lists `HOUSTON` (`L6AQ95CN5NG4W`, ACTIVE) | New store profile only; no new credential |
| Warehouse | 86 of 132 `bhaga` objects have no `store` column (live `INFORMATION_SCHEMA`) | Do not migrate them |
| Pipeline | Dataset, Firestore collection and store are all env/arg driven (`core/datastore.py:30`, `skills/bhaga_config/state_adapter.py:68`, `agents/bhaga/scripts/daily_refresh.py:2769`); migrations are rewritten per dataset (`core/datastore.py:292`) | A second dataset is a config change |
| ADP | `_select_company` already picks a company by Client ID (`skills/adp_run_automation/runner.py:535`) | Houston needs `adp_run.iid` + `tenant_uuid` |

**Decision: one BigQuery dataset per store** (`bhaga` = Austin, `bhaga_houston` = Houston, same
schema from the same `core/migrations/`). Rejected: adding a `store` column to 48 tables and
rewriting 38 views and every MERGE key in a week, with Austin payroll numbers at risk. The combined
view reads both datasets and adds additive metrics; ratios are recomputed from summed parts.

## Milestone 1 — Store switcher + per-store dataset in the console

Model: Sonnet (feature work); Opus for the write-guard review.

Files:

- `apps/operator-console/lib/config/stores.ts:5` — replace `STORE_DISPLAY` with a registry:

```ts
export type StoreId = "palmetto" | "houston";
export const STORES: Record<StoreId, { label: string; dataset: string }> = {
  palmetto: { label: "Austin", dataset: "bhaga" },
  houston: { label: "Houston", dataset: "bhaga_houston" },
};
export const STORE_COOKIE = "oc_store";
export function parseStore(raw: unknown): StoreId; // unknown value -> "palmetto"
```

- New `apps/operator-console/lib/config/current-store.ts` (server-only):
  `export async function currentStore(): Promise<StoreId>` — reads `oc_store` via `cookies()`.
- `apps/operator-console/lib/bq/client.ts:15` — `fq()` keeps emitting the default dataset;
  `q()`, `mutate()` and `submitQuery()` call `forStore(sql, await currentStore())`, which rewrites
  `` `PROJECT.bhaga. `` to the selected store's dataset (same rule as `_rewrite_dataset`). One place,
  so no query can miss it.
- `apps/operator-console/lib/bq/cache/deps.ts:29` and `cache/index.ts:63` — accept the statement's
  own dataset; version docs for a non-default dataset are keyed `<dataset>.<table>`.
- `apps/operator-console/lib/auth/identity.ts:31` — `DEFAULT_STORE` stays as the fallback only; every
  page/action call site (`app/*/page.tsx`, `app/*/actions.ts`, `components/shell/Topbar.tsx:60`)
  switches to `await currentStore()`.
- `apps/operator-console/components/shell/StoreFilter.tsx:5` — becomes a client `FilterSelect`-style
  dropdown (`components/ui/select.tsx`, same trigger size/tokens as the Period filter, 44px tap
  target on phone) that sets the cookie and `router.refresh()`es. D1/D2: the existing top-bar chip is
  enhanced, no second control.
- Wrong-store write guard: new `components/shell/StoreGuard.tsx` (client) rendered in
  `app/layout.tsx` with the store the page rendered for; on focus/visibility change, a cookie
  mismatch triggers `router.refresh()`. `lib/actions/useConsoleAction.ts` refuses to run when the
  cookie no longer matches ("This tab was showing Austin — reloaded to Houston. Nothing was saved.").
- Out-of-scope screens (Order Quality, Automations) show a plain "Austin only for now" note on Houston.

**Verify (M1):**

```bash
cd apps/operator-console && npm run lint && npm test && npm run build
python3 apps/operator-console/scripts/capture_evidence.py --path /home --label i533-home-houston
```

Pass: unit tests for `parseStore`, `forStore` (rewrites only dataset qualifiers, never params),
guard refusal; Austin Home/Sales/Labor/Payroll numbers identical before/after for one period.

## Milestone 2 — Houston pipeline (Square + ADP) into `bhaga_houston`

Model: Sonnet; Opus for the dataset guard.

- New `agents/bhaga/knowledge-base/store-profiles/houston.json` — `store_id`, `display_name`,
  `bq_dataset: "bhaga_houston"`, `firestore_collection: "runs_houston"`, `square.location_id`,
  `square.oauth_secret: "square_palmetto_oauth"`, `adp_run.iid` / `tenant_uuid` (from the ADP spike).
  Add `bq_dataset: "bhaga"` to `palmetto.json`.
- `skills/square_api/auth.py:58` — secret name from the profile's `square.oauth_secret`
  (one Square account serves both stores), default unchanged.
- `agents/bhaga/scripts/daily_refresh.py:2769` — before any write, assert
  `datastore.dataset() == profile["bq_dataset"]` and the Firestore collection matches; fail with
  `store_dataset_mismatch store=<s> dataset=<d> expected=<e>`.
- Cloud Scheduler `bhaga-nightly-houston` (21:45 CT) → same job with overrides
  `BHAGA_STORE=houston`, `BHAGA_BQ_DATASET=bhaga_houston`, `BHAGA_FIRESTORE_COLLECTION=runs_houston`
  and args `--store houston` (`Dockerfile:54` default stays `palmetto`).
- `agents/bhaga/scripts/status.py` — `--store houston` reads the Houston dataset.

**Verify (M2):**

```bash
python3 -m pytest agents/bhaga/scripts skills/square_api core -q
BHAGA_DATASTORE=bigquery BHAGA_BQ_DATASET=bhaga_houston python3 -m agents.bhaga.scripts.status --store houston
```

Pass: a real Houston run with Square + ADP row counts; run log shows `step=select-company iid=<houston>`;
a deliberately mismatched store/dataset run exits non-zero before writing.

## Milestone 3 — Labor, Inventory, Payroll, Accounting on Houston

Model: Sonnet.

- Remove hardcoded `'palmetto'` in `apps/operator-console/lib/bq/queries.ts:2536` (order reco) and
  the views it reads (new migration `098_order_reco_store_param.sql`, same output for Austin).
- `apps/operator-console/lib/payroll/openPeriod.ts:12` — anchor/period length from `store_config`.
- `apps/operator-console/lib/config/stores.ts:10` — ADP tenant per store.
- Inventory closing-count source for Houston per the operator's tracker choice.

**Verify (M3):**

```bash
cd apps/operator-console && npm test && npm run build
python3 scripts/verify.py --full
```

Pass: each Houston screen cross-checked against a raw query; a goal edit and a restock edit on
Houston leave `bhaga.store_config` / `bhaga.inventory_restock_schedule` row counts and max
`updated_at` unchanged.

## Milestone 4 — Combined both-stores view

Model: Sonnet.

- Third switcher option "Both stores" (read-only): Home and Sales run each query once per store
  and merge with `lib/kpi/combine.ts` (sum additive fields, recompute ratios).
- Mutating controls are hidden in the combined view.

**Verify (M4):**

```bash
cd apps/operator-console && npm test
```

Pass: combined net sales / orders / labor $ equal Austin + Houston for the same period.

## Per-scenario evidence (PR §4, operator-approved)

Evidence tier: sandbox-live
scenario: full-live (Austin regression) plus a live Houston run into `bhaga_houston`

1. Happy path — switcher screenshots (hosted https) for each in-scope screen on both stores.
2. Legacy — Austin numbers identical before/after.
3. Happy path — Houston pipeline run, row counts, `status --store houston`.
4. Happy path — Houston screens match raw queries.
5. Failure — stale tab after a store switch: save is refused, nothing written.
6. Failure — store/dataset mismatch run exits before writing.
7. Recovery — re-running a Houston date upserts, no duplicates.
8. Combined totals = Austin + Houston.
9. ADP opens the Houston company by Client ID.

## Invariants preserved

Idempotent upserts by natural key; money as integer cents in the pipeline; America/Chicago for every
date boundary (Houston profile `shop_tz`, and Square's location time zone must be Central);
read-only toward ADP payroll Submit; sandbox isolation guards unchanged; Austin's `bhaga` dataset
receives no schema or data change from this work; append-only history tables stay append-only.

## Feature flag

Can it silently produce wrong numbers? For Austin, no: the default store and dataset are unchanged
and the rewrite is a no-op for `palmetto`. Houston is new, so no flag; the switcher lists Houston
only when `CONSOLE_STORES` includes it (runtime env, default `palmetto,houston`), recorded in
`docs/FEATURE_FLAGS.md`.

## Docs lock-step

`RUNBOOK.md` (Houston scheduler, dataset, collection, status), `agents/bhaga/scripts/README.md`,
`docs/operator-console/ARCHITECTURE.md` §1 + new "Stores" section, `.cursor/rules/bhaga.mdc`
(store profile source of truth), `agents/bhaga/knowledge-base/DOMAIN.md`, `PROGRESS.md`; run
`python3 scripts/check_doc_freshness.py --base origin/main`.

## Branch / PR mechanics

One branch, PRs opened with `gh pr create --base main` as `jarvis-agent-bot328`; never self-merge;
reply to every review comment; push with `--no-verify` only after a local `verify.py --full`.
Milestones 1–2 ship as the first PR so the switcher and Houston data are live before opening day;
3 and 4 follow on the same worktree. Operator demo (localhost screenshots) before each PR.
