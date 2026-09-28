# Issue #350 — Order reco: accurate, fast, honest, auditable

Branch: `fix/i-was-updating-the-orders-for` · Tracking: [#350](https://github.com/aditya2kx/jarvis/issues/350)
(scope amendment: issuecomment-5873208071) · One PR (operator decision 2026-09-28 10:29 CT).

Consulted: `CONTRIBUTING.md` (dev loop = success criteria), `docs/WORKFLOW.md`,
`.cursor/rules/bhaga-principles.mdc` (B1 sandbox-first, B3 breadcrumbs),
`.cursor/rules/plan-execution-readiness.mdc`, `docs/contributing/sandbox-evidence.md`,
`docs/contributing/ui-polish.md`, `docs/FEATURE_FLAGS.md`, `user-preferences.mdc` (#19 mechanical
gates, #27 UI polish, #29 runtime tunables, J2 sandbox + prod evidence, J5 reuse scenarios).

## As-built deviations (read these first — the sections below are the pre-build plan)

- **Migrations are 080/081**, not 076/077 (prod already applied 076/077 from another branch).
- **No `inventory_order_reco_inputs` table.** The inputs of each run live in
  `inventory_order_reco_runs.inputs` (JSON), next to its fingerprint and capacity — one ledger instead of two.
- **Swap is a single `MERGE … ON FALSE`** (insert new generation, delete the store's old rows) inside the
  transaction, not INSERT + DELETE — fewer statements, 43 s → ~19 s to commit in the sandbox.
- **`inventory_edit_log` has no `old_value` column.** Each row stores the full post-edit state;
  `vw_inventory_edit_log` derives `old_value` with `LAG`, so a writer can never record a wrong "before".
- **No `core/edit_log.py`.** The only Python writer of reco inputs is the webhook, which cannot import
  `core/`; it has `handler._log_inventory_edit` instead (same row shape as `lib/bq/editLog.ts`).
- **Drawer preview is client-side** (`lib/inventory/waterFill.ts`), not a dry-run CALL: a dry run took
  ~30 s, too slow to follow typing. The procedure's dry-run mode still exists and the sandbox `preview`
  scenario proves it writes nothing. Browser, Python reference and procedure agree via the shared
  golden fixture + sandbox parity.
- **Change summary is per-date TOTAL** ("10/09: 29 → 31 tubs") from history, not an inputs diff.
- **Nightly shadow compare is a follow-up**, not in this PR: the legacy chain can only write the live
  table, so shadowing needs its own dry-run path. The legacy flag's removal condition is instead
  "≥ 14 nightlies with no `order_reco_failed` breadcrumb" (`docs/FEATURE_FLAGS.md`).
- **Tie-break correction:** the 10/09 Matcha/Mango "exact tie" seen in chat was float noise, not a tie.
  Exact ties are still possible and were undetermined in the TVFs, so the deterministic
  `ORDER BY sort_key, item, k` tie-break stays (golden case 5 covers a real tie).

## Diagnosis (prod, read-only, 2026-09-28)

| Symptom | Evidence | Root cause |
|---|---|---|
| `Manual 3` after pinning Açaí 12 | operator screenshot; pins `updated_at` 14:54:51Z, generation 14:58:41Z | `Source` computed live from `inventory_order_tub_overrides` (`apps/operator-console/lib/bq/queries.ts:2089-2101`) while numbers come from materialized `inventory_order_reco` |
| 3–4 min Apply → numbers | job `hmbdp` created 15:18:28Z, container start 15:21:03Z, done 15:21:28Z | Interactive refresh runs the full `bhaga-daily-refresh` Cloud Run **job** (`apps/operator-console/lib/bhaga/recompute.ts:126-133`) — 2m35s provisioning for 25s of work |
| 25s of work for ~180 KB | TVF slot1 7.4s / 1.37M slot-ms, slot2 10.3s / 1.65M slot-ms, view alone 2.7s | CTE `oa` (the view) re-inlined ~4× per TVF (`core/migrations/067_order_reco_write_then_swap.sql:30-43`) |
| Banner gone after 25s | poll queries stop 14:55:17Z | poll lives in drawer hook; drawer unmounts (`EstimateTubsDrawer.tsx:175`, cleanup `useOrderRecoRefreshFollowup.ts:48`) |
| Reload shows no staleness | `page.tsx:121-124` | `recoPending` only when an old generation is *incomplete*; a complete old one paints as fresh |
| Edit during a run can be dropped | `recompute.ts:128-130` returns `started:false`; callers ignore | "skip if any job running", no dirty/coalesce |
| 3 recompute copies, none atomic | `writes.ts:156-202`, `core/order_reco.py:57`, `cloud/webhook/handler.py:1322` | duplication; INSERT/INSERT/DELETE as separate DML jobs |
| Hardcoded store | `067_...sql:23,42,47` `store = 'palmetto'` | TVFs ignore store (Houston launching) |
| History lost | swap DELETE `writes.ts:198-201`; pins delete-then-insert `writes.ts:94`; `store_config` overwritten (110 row gone) | no append-only record; "it said 34 an hour ago" unanswerable |
| Pins silently move Estimated bases | Matcha 7→6, Mango 7→5 after pins | by design (capacity hard cap, operator-confirmed) but invisible |

Operator decisions: capacity stays a hard cap, reallocation made **visible**; history **append-only,
never deleted**; all of the above in **one PR**.

History interpretation (flag for operator at PR review): live "current state" tables
(`inventory_order_tub_overrides`, `inventory_restock_orders`, `store_config`, …) keep updating in
place so every reader stays unchanged; **every** change to them is appended to `inventory_edit_log`,
and every reco generation + its full inputs are appended to history tables. No statement anywhere may
`DELETE`/`UPDATE`/`TRUNCATE` a history table — enforced by `scripts/check_append_only_history.py`.

## Invariants (must not break)

- Integer tubs; `on_hand + order ≤ capacity` for Estimated dates; Actuals win whole-date, shown verbatim.
- Water-fill semantics bit-identical to migration 067 (proven by parity, M4) — pins consume budget first.
- America/Chicago for as-of date; slot 1 burn = `DATE_DIFF(d1, as_of)`.
- Idempotent: re-running the procedure with unchanged inputs yields identical numbers (new generation id only).
- `inventory_order_reco` holds exactly one generation per `(store, Slot, Item)` after commit —
  `ensureOrderRecoFresh` `hasDupes` (`writes.ts:238-249`) must stay quiet.
- No side effect is ever reflexively retried; failures leave a greppable breadcrumb
  `BREADCRUMB order_reco run_id=… store=… status=failed err=…` in Cloud Run logs **and** a `failed`
  row in `inventory_order_reco_runs`.
- Read-only ADP/Square untouched; no hardcoded store/sheet ids (store is a parameter everywhere).

## Feature flag decision

Can it silently produce wrong numbers? **Yes** — the recompute is reimplemented. So:
`BHAGA_ORDER_RECO_LEGACY=1` (Python env, core + webhook) / `FEATURES.orderRecoLegacy` (console)
restores the old TVF path for instant rollback. Default **off** (new procedure) once M4 parity passes.
Add a row to `docs/FEATURE_FLAGS.md`; safe to remove after 14 days of prod runs with parity-clean
nightly shadow compare. `FEATURES.asyncOrderReco` (`features.ts`) is superseded and removed in this PR
(its row in `docs/FEATURE_FLAGS.md` marked removed). Everything else is additive (new tables, new column).

---

## M1 — Accurate core: one atomic procedure + append-only history

Model: **Sonnet 5 medium** (SQL-heavy; Opus reviews the procedure diff).

### 1a. Migration `core/migrations/080_order_reco_history.sql` (DDL)

```sql
-- Append-only. Nothing may DELETE/UPDATE/TRUNCATE these (check_append_only_history.py).
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_order_reco_history` (
  store STRING NOT NULL, run_id STRING NOT NULL, refreshed_at TIMESTAMP NOT NULL,
  Slot INT64, Item STRING, delivery_date DATE,
  `Current Qty` FLOAT64, `Avg per day` FLOAT64, `On Hand at Restock` FLOAT64,
  `Order Tubs` INT64, `Order Weight lbs` FLOAT64, `After Restock` FLOAT64,
  `Days Left After Restock` FLOAT64, _ord INT64, Source STRING
) PARTITION BY DATE(refreshed_at) CLUSTER BY store, delivery_date;

-- One row per state transition (requested/running/committed/superseded/failed/noop).
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_order_reco_runs` (
  run_id STRING NOT NULL, store STRING NOT NULL, status STRING NOT NULL,
  event_at TIMESTAMP NOT NULL, trigger STRING, requested_by STRING,
  refreshed_at TIMESTAMP, inputs_fingerprint INT64, capacity INT64, as_of_date DATE,
  passes INT64, bq_job_id STRING, error STRING,
  inputs JSON  -- committed rows only: pins, actuals, current-qty overrides, live dates, capacity
) PARTITION BY DATE(event_at) CLUSTER BY store, run_id;

-- Per-item inputs snapshot for each generation ("avg/day changed overnight" diffs).
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_order_reco_inputs` (
  store STRING NOT NULL, run_id STRING NOT NULL, refreshed_at TIMESTAMP NOT NULL,
  item STRING, current_qty FLOAT64, avg_daily_usage FLOAT64, reported STRING,
  days_considered STRING, excluded_days STRING
) PARTITION BY DATE(refreshed_at) CLUSTER BY store;

-- Who changed what, when (console + Slack + pipeline write paths).
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_edit_log` (
  event_id STRING NOT NULL, store STRING NOT NULL,
  entity STRING NOT NULL,   -- order_tub_override | restock_order | restock_schedule |
                            -- current_qty_override | usage_day_override | store_config
  action STRING NOT NULL,   -- set | clear | replace | move | remove
  delivery_date DATE, item STRING, key STRING,
  old_value STRING, new_value STRING,
  edited_by STRING, edited_at TIMESTAMP NOT NULL, source STRING  -- console | slack | pipeline
) PARTITION BY DATE(edited_at) CLUSTER BY store, entity;

ALTER TABLE `jarvis-bhaga-prod.bhaga.inventory_order_reco` ADD COLUMN IF NOT EXISTS Source STRING;
ALTER TABLE `jarvis-bhaga-prod.bhaga.inventory_order_reco` ADD COLUMN IF NOT EXISTS run_id STRING;

-- Everything that can change the reco. Fingerprint catches deletes (max(updated_at) would not).
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_order_reco_inputs_fingerprint` AS
SELECT s.store,
  FARM_FINGERPRINT(CONCAT(
    IFNULL((SELECT STRING_AGG(FORMAT('%t|%s|%d', delivery_date, item, quantity_tubs), ',' ORDER BY delivery_date, item)
            FROM `jarvis-bhaga-prod.bhaga.inventory_order_tub_overrides` WHERE store = s.store), ''), '#',
    IFNULL((SELECT STRING_AGG(FORMAT('%t|%s|%t', delivery_date, item, quantity_tubs), ',' ORDER BY delivery_date, item)
            FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders` WHERE store = s.store), ''), '#',
    IFNULL((SELECT STRING_AGG(FORMAT('%t', delivery_date), ',' ORDER BY delivery_date)
            FROM `jarvis-bhaga-prod.bhaga.inventory_restock_schedule` WHERE store = s.store), ''), '#',
    IFNULL((SELECT STRING_AGG(FORMAT('%s|%t', item, quantity_units), ',' ORDER BY item)
            FROM `jarvis-bhaga-prod.bhaga.inventory_current_qty_overrides` WHERE store = s.store), ''), '#',
    IFNULL((SELECT STRING_AGG(FORMAT('%s|%t|%s', item, submitted_date, mode), ',' ORDER BY item, submitted_date)
            FROM `jarvis-bhaga-prod.bhaga.inventory_usage_day_overrides` WHERE store = s.store), ''), '#',
    -- Closings drive current_qty + avg/day. Aggregate the raw table, not the 2.7s assistant view,
    -- so the 2s status poll stays cheap.
    IFNULL((SELECT FORMAT('%d|%t|%t', COUNT(*), SUM(quantity_units), MAX(submitted_date))
            FROM `jarvis-bhaga-prod.bhaga.inventory_closing_daily` WHERE store = s.store), ''), '#',
    IFNULL((SELECT value FROM `jarvis-bhaga-prod.bhaga.store_config`
            WHERE store = s.store AND key = 'order_reco_max_tubs'), ''), '#',
    CAST(CURRENT_DATE('America/Chicago') AS STRING)
  )) AS inputs_fingerprint
FROM (SELECT DISTINCT store FROM `jarvis-bhaga-prod.bhaga.store_config`) s;
```

Column names for `inventory_usage_day_overrides` (`mode`, `submitted_date`) verified against
`writes.ts:954-998` before writing; adjust if the table uses different names.

### 1b. Migration `core/migrations/081_sp_refresh_order_reco.sql` (procedure)

Signature:
```sql
CREATE OR REPLACE PROCEDURE `jarvis-bhaga-prod.bhaga.sp_refresh_order_reco`(
  p_store STRING, p_trigger STRING, p_requested_by STRING, p_run_id STRING,
  p_dry_run BOOL, p_pins_override JSON  -- dry_run preview: [{delivery_date,item,quantity_tubs}] replaces pins
)
```
Body outline (BigQuery scripting):
1. `DECLARE v_pass INT64 DEFAULT 0; DECLARE v_fp INT64; DECLARE v_gen TIMESTAMP; DECLARE v_as_of DATE DEFAULT CURRENT_DATE('America/Chicago'); DECLARE v_cap INT64;`
2. Unless dry_run: `INSERT … runs (status='running')`.
3. `LOOP` (max 3 passes):
   - `SET v_fp = (SELECT inputs_fingerprint FROM vw_order_reco_inputs_fingerprint WHERE store = p_store);`
   - `SET v_cap = COALESCE((SELECT CAST(value AS INT64) FROM store_config WHERE store=p_store AND key='order_reco_max_tubs'), 120);`
   - `CREATE TEMP TABLE _oa AS SELECT … FROM vw_inventory_order_assistant WHERE store = p_store;` —
     **materialized once** (the 6× speed-up), also `_dates`, `_actuals`, `_pins` (from table, or from
     `p_pins_override` when dry_run).
   - Slot 1 → `_reco` from `_oa` (burn `DATE_DIFF(d1, v_as_of)`); for each slot ≥ 2 (`FOR s IN (SELECT slot FROM _dates WHERE slot >= 2 ORDER BY slot)`) chain from `_reco` rows of slot−1 **in this run** (not from the materialized table — removes the 067 cross-generation read). Water-fill identical to `067_...sql:51-119`, except `GENERATE_ARRAY(1, LEAST(tubs_budget, 300))`. `Source` column = `Actuals` / `Manual` / `Estimated` from the same `_actuals`/`_pins`.
   - If dry_run: `SELECT * FROM _reco ORDER BY delivery_date, _ord, Item; RETURN;`
   - `BEGIN TRANSACTION;` insert `_reco` into `inventory_order_reco` (new `v_gen`, `run_id`) **and** `inventory_order_reco_history`; insert `_oa` into `inventory_order_reco_inputs`; `DELETE FROM inventory_order_reco WHERE store=p_store AND refreshed_at != v_gen;` insert runs `committed` row with `inputs` JSON; `COMMIT TRANSACTION;`
   - `IF (SELECT inputs_fingerprint …) = v_fp OR v_pass >= 3 THEN LEAVE; END IF;` (coalesces edits made mid-run).
4. `EXCEPTION WHEN ERROR THEN` → `ROLLBACK` if in txn; if `@@error.message` contains `concurrent update` / `Transaction is aborted` → runs row `superseded` (the in-flight run's post-commit fingerprint check picks up our edit); else runs row `failed` with `@@error.message`, then `RAISE`.

`core/datastore.py:318` `_split_statements` must learn `BEGIN … END` depth (and `CREATE … PROCEDURE` bodies) so the procedure is one statement. Unit test in `core/test_datastore_split.py`.

### 1c. Edit log writes

New helpers, called in the same function as each write (after the mutation succeeds):
- TS: `apps/operator-console/lib/bq/editLog.ts`
  ```ts
  export async function logInventoryEdit(e: {
    store: string; entity: InventoryEditEntity; action: "set"|"clear"|"replace"|"move"|"remove";
    deliveryDate?: string; item?: string; key?: string;
    oldValue?: string | null; newValue?: string | null; by: string;
  }): Promise<void>
  ```
  Call sites in `writes.ts`: `clearRestockOrders:31`, `clearOrderTubOverrides:53`,
  `replaceOrderTubOverrides:65`, `replaceRestockOrders:121`, `submitRestock:290`, `moveRestockDate:322`,
  `removeRestockDate:380`, `replaceEstimatedRestockDate:403`, `setConfig:440`,
  `setUsageDayOverride:954`, `clearUsageDayOverride:984`, `setCurrentQtyOverride:1000`,
  `clearCurrentQtyOverride:1029`, `applyCurrentQtyOverrides:1047`, `clearCurrentQtyOverrides:1060`.
  Old values read in the same function before the mutation (one SELECT per call).
- Python: `core/edit_log.py`
  ```python
  def log_inventory_edit(store: str, entity: str, action: str, *, delivery_date: str | None = None,
                         item: str | None = None, key: str | None = None, old_value: str | None = None,
                         new_value: str | None = None, edited_by: str, source: str) -> None: ...
  ```
  Call sites: `cloud/webhook/handler.py:908` `_handle_config_set`, `:1283` `_restock_replace_orders`,
  `:1411` `_handle_restock_submission`.

### 1d. Mechanical gate `scripts/check_append_only_history.py` (wired into `scripts/verify.py`)

Fails if any `.py`/`.ts`/`.sql` outside `core/migrations/080_*`/`081_*` issues
`DELETE|UPDATE|TRUNCATE|MERGE … (inventory_order_reco_history|inventory_order_reco_runs|inventory_order_reco_inputs|inventory_edit_log)`,
**and** fails if an exported `writes.ts` function mutating an `inventory_*`/`store_config` table has no
`logInventoryEdit(` call (AST-free regex over function bodies — same approach as other `check_*.py`).

**Verify (M1)**
```bash
python3 -m pytest core/test_datastore_split.py core/test_migration_080_order_reco_history.py \
  core/test_migration_081_sp_refresh_order_reco.py core/test_edit_log.py -q
python3 scripts/check_append_only_history.py
BHAGA_BQ_DATASET=bhaga_sandbox_i350 python3 scripts/order_reco_sandbox.py provision   # clone prod inputs, apply 080/081
BHAGA_BQ_DATASET=bhaga_sandbox_i350 python3 scripts/order_reco_sandbox.py run --trigger m1-smoke
```
Pass criterion: tests green; gate exit 0; sandbox run writes exactly one live generation, one history
generation, one inputs snapshot, runs rows `running`→`committed`; procedure wall time ≤ 8s for 2 slots
(printed by the script).

---

## M2 — Fast and never skipped

Model: **Sonnet 5 medium**.

- New `apps/operator-console/lib/bhaga/orderReco.ts`:
  ```ts
  export async function requestOrderRecoRefresh(opts: {
    store: string; trigger: string; requestedBy: string;
  }): Promise<{ runId: string; jobId: string }>
  // inserts runs 'requested', submits `CALL sp_refresh_order_reco(@store,@trigger,@by,@run_id,FALSE,NULL)`
  // via BigQuery createQueryJob WITHOUT awaiting results (BQ runs it server-side), records bq_job_id.
  export async function orderRecoStatus(store: string): Promise<OrderRecoStatus>
  export type OrderRecoStatus = {
    state: "idle" | "running" | "stale" | "failed";
    runId: string | null; startedAt: string | null; trigger: string | null;
    lastCommittedAt: string | null; lastLatencyMs: number | null; p95LatencyMs: number | null;
    liveFingerprint: string; paintedFingerprint: string | null; error: string | null;
  };
  // one query: latest runs rows + vw_order_reco_inputs_fingerprint. stale = fingerprints differ and nothing running.
  ```
- Replace every `triggerOrderRecoRefresh` / `maybeQueueOrderReco` / `refreshOrderReco` call in
  `apps/operator-console/app/inventory/actions.ts:33-80, 250-270, 284-290, 310-370` and
  `page.tsx:96-108` with `requestOrderRecoRefresh`. `ensureOrderRecoFresh` (`writes.ts:218`) becomes
  "request if status is stale" (no inline TVFs, no Cloud Run job).
- Delete `refreshOrderReco` (`writes.ts:156-202`), `triggerOrderRecoRefresh`/`orderRecoOnlyEnv`
  (`recompute.ts:75-86,126-133`), webhook `_refresh_order_reco` body (`handler.py:1322-1410`) →
  `core.order_reco.request_refresh(store, trigger, by, wait=False)`.
- `core/order_reco.py:57` `refresh_order_reco(store)` → `CALL` with `wait=True` (nightly
  `daily_refresh.py:2638-2644` keeps working); legacy TVF body kept only under `BHAGA_ORDER_RECO_LEGACY=1`.
- `pollOrderRecoRefreshAction` (`actions.ts:385-409`) → `orderRecoStatusAction()` returning `OrderRecoStatus`.
- Latency: `status.py` freshness table gains an `order_reco_runs` row (last run, p95 of last 20, failures 24h);
  `apps/operator-console/lib/bhaga/health.ts` sets a warning when p95 > 60s or last run `failed`
  (surfaces in the existing console health banner).

**Verify (M2)**
```bash
cd apps/operator-console && npx vitest run __tests__/order-reco-status.test.ts __tests__/order-reco-request.test.ts
python3 -m pytest core/test_order_reco.py cloud/webhook/test_handler.py -q -k "order_reco"
BHAGA_BQ_DATASET=bhaga_sandbox_i350 python3 scripts/order_reco_sandbox.py latency --runs 10
BHAGA_BQ_DATASET=bhaga_sandbox_i350 python3 scripts/order_reco_sandbox.py concurrency
```
Pass criterion: p95 request→committed ≤ 30s over 10 runs; concurrency scenario (edit A, request, edit B
mid-run, request) ends with a committed generation whose fingerprint equals the live fingerprint and a
`superseded` or 2-pass run row — no edit lost; failure scenario (procedure forced to error via
`p_trigger='force-error-test'` guard) leaves a `failed` row + breadcrumb and no partial live generation.

---

## M3 — Honest page

Model: **Sonnet 5 medium** (UI); polish review against `docs/contributing/ui-polish.md`.

Reuse design-system primitives: `components/ui/badge.tsx`, `components/ui/tooltip.tsx`,
`components/ui/sheet.tsx`, `DataTable`, muted-foreground text, existing amber banner tone from
`InventoryRecoFreshness.tsx`. No new ad-hoc styles.

1. **Same-generation truth** — `orderRecoSlots` (`queries.ts:2069-2110`) reads materialized
   `r.Source` + `r.run_id`; drop the live `EXISTS` subqueries. New `pendingPins(store)` query returns
   live pins/actuals whose values differ from the painted generation; `OrderRecoTable.tsx:184-216` renders
   those cells with the painted number muted + `Badge variant="secondary"` "Updating" (spinner icon),
   tooltip "Your pin (12) applies when the refresh finishes". A `Manual` chip only ever sits next to a
   number from the same generation.
2. **Page-level status (survives reload / drawer close)** — `InventoryRecoFreshness` driven by
   `OrderRecoStatus`: running → "Updating order recommendation · started 12s ago · capacity change";
   stale → "Your latest change hasn't been applied yet — refreshing" (auto-requests); failed → destructive
   tone + error + "Retry" button (explicit operator action, never auto-retry). Poll `orderRecoStatusAction`
   every 2s from a page-level provider (`lib/inventory/OrderRecoStatusProvider.tsx`) mounted in
   `page.tsx`, not in drawers; drawers call `followOrderReco` on the provider. On commit: toast
   "Updated 9s after your edit" + `router.refresh()`.
3. **Safe-to-edit** — `CapacityEdit`, `EstimateTubsDrawer`, `CurrentQtyDrawer`, `UsageDayOverrideDrawer`
   show an inline muted note while running: "A refresh is in progress — your change will apply right after
   it." Apply stays enabled (coalescing guarantees application).
4. **Visible reallocation** — for the latest edit-triggered generation vs its predecessor (from
   `inventory_order_reco_history`, ≤ 24h old), Estimated cells whose tubs changed show a small delta chip
   ("−2") with tooltip "Recomputed because pins on 10/09 changed (Açaí 3→12, Pitaya 0→2, Pog 11→3)".
   `EstimateTubsDrawer` gains **Preview impact** (calls `sp_refresh_order_reco(... p_dry_run=TRUE, p_pins_override)`)
   listing which Estimated bases would move before Apply.
5. **What changed since last generation** — one line under the banner from `inputs` JSON +
   `inventory_order_reco_inputs` diff: "Capacity 110→112 · Avg/day updated from 09/27 closing (Açaí 1.66→1.73)".
6. **Actuals note** — tooltip on `Actuals` Source and on TOTAL After restock when it exceeds capacity:
   "Placed order — shown as uploaded, not capped by capacity."

Interaction states: hover + focus-visible tooltips on every chip, keyboard-reachable; pending spinner
respects `prefers-reduced-motion`; mobile tap on chip opens tooltip; banner is `aria-live="polite"`.

**Verify (M3)**
```bash
cd apps/operator-console && npx vitest run __tests__/order-reco-table-pending.test.tsx \
  __tests__/order-reco-status-banner.test.tsx __tests__/order-reco-reallocation.test.ts \
  __tests__/estimate-tubs-drawer.test.tsx __tests__/order-reco-pivot.test.ts
npx tsc --noEmit && npx eslint .
```
Pass criterion: the torn state (live pin ≠ painted) never renders a `Manual` chip beside a stale number
(test asserts `Manual 3` unreachable); banner visible after unmounting the drawer and after a fresh
render with stale fingerprint; delta chips exactly Matcha −1 / Mango −2 for the 09-28 fixture.

---

## M4 — Proof and cutover

Model: **Opus 4.8 medium** for parity review + plan review; **Sonnet** for scripts/docs.

- `core/order_reco_reference.py` — pure-Python reference water-fill
  (`def compute_reco(inputs: RecoInputs) -> list[RecoRow]`), the permanent oracle.
- Golden fixture `core/testdata/order_reco_2026-09-28.json` (inputs captured today) with expected:
  | Scenario | 10/02 | 10/09 |
  |---|---|---|
  | pre-pin, cap 110 | 41 (Actuals) | 29: Açaí 3 Pitaya 0 Pog 11 Matcha 7 Mango 7 Ube 1 |
  | pins Açaí 12/Pitaya 2/Pog 3, cap 110 | 41 | 29: Matcha 6 Mango 5 Ube 1 |
  | same pins, cap 112 | 41 | 31: Matcha 7 Mango 6 Ube 1, after 111.68 |
  | cap 60 (budget ≤ pins) | 41 | 17: pins only, unpinned 0 |
- `scripts/order_reco_sandbox.py parity` — in `bhaga_sandbox_i350` (zero-copy `CLONE` of prod input
  tables; prod is never written), run legacy TVF path and the procedure over a scenario matrix: pins
  variants, capacity {60,100,110,112,120,200}, Actuals/no-Actuals, 1–4 live slots, as-of day rollover.
  Every cell must match the reference model and the legacy path (legacy compared with `as_of = CURRENT_DATE`).
- Nightly shadow compare in prod for 14 days (flag-removal condition): after the nightly procedure
  commits, `daily_refresh` runs the legacy TVFs as read-only `SELECT`s and compares every cell to the
  committed generation; any diff logs `BREADCRUMB order_reco_shadow_diff run_id=… cells=N`.
- Docs lock-step: `agents/bhaga/knowledge-base/DOMAIN.md` (new tables, procedure, Source column,
  fingerprint), `RUNBOOK.md` (order reco ops: status, runs table, legacy flag, breadcrumbs),
  `agents/bhaga/scripts/README.md` (`core/order_reco.py`, `order_reco_sandbox.py`),
  `docs/operator-console/ARCHITECTURE.md` (status provider, no Cloud Run job on edits),
  `docs/FEATURE_FLAGS.md`, `PROGRESS.md` dated entry; run `python3 scripts/check_doc_freshness.py --base origin/main`.

**Verify (M4)**
```bash
python3 -m pytest core/test_order_reco_reference.py core/test_order_reco_golden.py -q
BHAGA_BQ_DATASET=bhaga_sandbox_i350 python3 scripts/order_reco_sandbox.py parity --matrix full
python3 scripts/verify.py --full
python3 scripts/check_doc_freshness.py --base origin/main
```
Pass criterion: 0 cell diffs across the matrix; golden scenarios exact; verify green.

---

## PR §4 evidence (acceptance contract)

Evidence tier: sandbox-e2e

Per-scenario evidence, captured into PR §4:
1. **Happy path — pin edit**: sandbox console (`BQ_DATASET=bhaga_sandbox_i350 BYPASS_IAP_EMAIL=… npm run dev`)
   pin Açaí 12 / Pitaya 2 / Pog 3 → screenshot sequence via
   `python3 apps/operator-console/scripts/capture_evidence.py --path /inventory --label i350-pending` (Updating
   chip + banner, no `Manual 3`) and `--label i350-committed` (Manual 12, delta chips Matcha −1 Mango −2,
   "Updated Ns after your edit").
2. **Happy path — capacity**: 110→112 → 10/09 31 tubs, after 111.68; "what changed: Capacity 110→112".
3. **Reload during refresh**: screenshot after hard reload mid-run shows banner (was blank before).
4. **Edit during a running refresh (recovery)**: `order_reco_sandbox.py concurrency` output — final
   fingerprint == live, no edit lost.
5. **Failure**: forced error → `failed` run row + `BREADCRUMB order_reco … status=failed` + console
   destructive banner with Retry; live table unchanged (atomicity).
6. **Latency**: `order_reco_sandbox.py latency --runs 10` table, p95 ≤ 30s (baseline 4 min).
7. **Accuracy**: `parity --matrix full` 0 diffs; golden test output.
8. **History**: queries showing two generations in `inventory_order_reco_history`, inputs diff, and
   `inventory_edit_log` rows for each edit with old→new; `check_append_only_history.py` exit 0.
9. **Legacy / idempotency**: re-run procedure with unchanged inputs → identical numbers, new generation only;
   `BHAGA_ORDER_RECO_LEGACY=1` path still produces the same numbers.
10. **Prod post-merge (J2)**: after deploy applies 080/081, operator-visible check on
    https://operator-console-887772634501.us-central1.run.app/inventory — current numbers unchanged
    (10/02 41, 10/09 31 at cap 112), one real edit lands in < 30s, runs + history rows present.

## Branch / PR mechanics

Single branch `fix/i-was-updating-the-orders-for` → one PR via
`gh pr create --base main --head fix/i-was-updating-the-orders-for` as `jarvis-agent-bot328`
(GH_TOKEN), body `Refs #350`, §4 filled from the evidence above. Push with `--no-verify` only after
`python3 scripts/verify.py --full` is green locally. Seed cost ledger (`pr_cost_ledger.py bind-pr` + `sync`),
then babysit per `pr-workflow.mdc` (batch fixes, reply on every thread, one push per loop). Never
self-merge; operator squash-merges. Remove the sandbox dataset `bhaga_sandbox_i350` after evidence is captured.

## Model routing summary

M1 Sonnet (Opus reviews procedure) · M2 Sonnet · M3 Sonnet · M4 Opus parity review + Sonnet docs.
One chat per PR (cost playbook, `docs/contributing/cost.md`).
