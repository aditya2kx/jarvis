# Palmetto Operator Console — Execution Plan (implementation-ready)

> Written so a **weaker/cheaper model (Sonnet / Composer)** can implement each step
> without opening any file not cited here. Companion to
> [`ARCHITECTURE.md`](ARCHITECTURE.md), [`PLAN.md`](PLAN.md), [`COST.md`](COST.md).
>
> **Golden rule:** the app renders BQ views and writes via the same MERGE contracts
> as `cloud/webhook/handler.py`. **No metric math in the app.** New number → new BQ
> view, never app logic.

---

## 0. How to use this doc

- Work milestone by milestone (M1 → M5). Each milestone is independently verifiable.
- After each milestone, run its **Verify** block; do not proceed until it passes.
- All new code lives under `apps/operator-console/` unless a step says otherwise.
- Commit per milestone on the branch `fix/i132-create-a-website-to-replace-grafana`.
- Do **not** hardcode secrets, project IDs in code (use env), or store metrics.
- When a dependency version is needed, install **latest** with npm (`pkg@latest`);
  known-good majors are noted. Do not invent pinned versions.

---

## 1. Locked decisions (do not re-litigate)

| Decision | Value |
|---|---|
| Framework | Next.js 15 App Router (React 19), TypeScript |
| UI | Tailwind CSS v4 + shadcn/ui + lucide-react |
| Charts | Recharts (v2 major) via shadcn Chart |
| Tables | TanStack Table (v8 major) — column pinning for frozen cols |
| Client fetch | TanStack Query (v5) only where interactive |
| Warehouse | `@google-cloud/bigquery` (v7 major), server-only |
| Auth | Google IAP (Cloud Run **direct** IAP — no dedicated LB; see COST.md) |
| Host | Cloud Run, container (`output: 'standalone'`) |
| Repo location | `apps/operator-console/` (this repo) |
| Goals store | `store_config` (BQ), weekly + monthly keys |
| Recognition bonuses | new `recognition_bonuses` table (migration 033) |
| LLM parse | Gemini (native GCP) |
| Delivery | one PR, screens behind `FEATURES` flags |
| BQ project / dataset | `jarvis-bhaga-prod` / `bhaga` (from env, matches `cloud/webhook/handler.py`) |

---

## 2. Confirmed BQ objects (exact names — verified in `core/migrations/`)

Read these; never guess a name. Grain/columns are in
`agents/bhaga/knowledge-base/DOMAIN.md`.

| Purpose | Object | Migration |
|---|---|---|
| Sales/labor daily | `vw_model_labor_daily` | 005 |
| Labor weekly | `vw_model_labor_weekly` | 005 |
| Payroll per period | `vw_model_payroll_period` | 005 |
| Review-bonus detail | `vw_review_bonus_detail` | 026 |
| Forecast | `vw_model_forecast` | 015 |
| Forecast accuracy | `vw_forecast_accuracy` | 011 |
| Forecast exclusions | `vw_forecast_exclusions` | 014 |
| Order quality daily | `vw_order_quality_daily` | 005 |
| KDS p95 by source | `vw_kds_order_quality_by_source_daily` | 025 |
| Inventory base analytics | `vw_inventory_order_assistant` | 028 |
| Order assistant table | `vw_order_assistant_table` | 029 |
| **Dual-date reco (combined)** | `vw_order_reco_combined` | 032 |
| Next 2 delivery dates | `vw_order_reco_next_dates` | 031 |
| Restock schedule (write) | `inventory_restock_schedule` | 030 |
| Restock actuals (write) | `inventory_restock_orders` | 030 |
| Reco materialized table | `inventory_order_reco` | 031 |
| Item volume | `square_item_daily` | 001 |
| Store config (goals/capacity, write) | `store_config` | 007 |
| Training shifts (write) | `training_shifts` | 020 |
| Employee aliases (write) | `employee_aliases` | 020 |
| Pipeline runs | `vw_pipeline_runs` | 019 |
| Source freshness | `vw_source_pulls` | 018 |

`store_config` keys already used: `order_reco_max_tubs` (capacity, default 120).
New goal keys (this project): `goal_net_sales_weekly`, `goal_net_sales_monthly`,
`goal_labor_pct_max`, `goal_food_cost_pct_max`, `goal_speed_on_time_pct_min`,
`goal_inventory_runway_days_min`.

---

## 3. Repo layout to create

```
apps/operator-console/
  package.json  tsconfig.json  next.config.ts  Dockerfile  .dockerignore
  .env.example
  app/
    layout.tsx                 # root shell: sidebar + topbar + store filter
    globals.css                # tailwind v4 entry
    page.tsx                   # redirect → /home
    home/page.tsx
    sales/page.tsx
    labor/page.tsx
    forecast/page.tsx
    order-quality/page.tsx
    payroll/page.tsx
    inventory/page.tsx
    pipeline/page.tsx
    api/parse-restock/route.ts # Gemini CSV/photo → rows (M3)
  components/
    shell/{Sidebar,Topbar,StoreFilter}.tsx
    ui/…                       # shadcn generated
    charts/{LineChartCard,BarChartCard,GoalLine}.tsx
    tables/DataTable.tsx       # TanStack wrapper w/ column pinning
    kpi/{HealthScorecard,KpiStat,GoalBar}.tsx
    drawers/{GoalsDrawer,TrainingQuickAdd,RecognitionDrawer,RestockImportDrawer}.tsx
  lib/
    bq/client.ts               # BigQuery singleton
    bq/queries.ts              # one typed fn per view (SELECT * FROM vw_*)
    bq/writes.ts               # MERGE/replace fns (mirror handler.py)
    auth/identity.ts           # IAP email extraction + store scope
    config/features.ts         # feature flags
    format.ts                  # cents→$, pct, dates (America/Chicago)
  __tests__/                   # vitest unit tests
docs/operator-console/         # these docs
core/migrations/033_recognition_bonuses.sql   # new (M4)
.github/workflows/operator-console-deploy.yml # new (M1)
```

---

## 4. Milestones

### M1 — Foundation (model: Sonnet)

**Goal:** app boots, renders one real view, deploys to Cloud Run behind IAP.

Steps:
1. Scaffold: from repo root,
   ```bash
   npx create-next-app@latest apps/operator-console --ts --app --tailwind --eslint --no-src-dir --import-alias "@/*"
   cd apps/operator-console
   npx shadcn@latest init -d
   npm i @google-cloud/bigquery@latest @tanstack/react-table@latest @tanstack/react-query@latest recharts@latest lucide-react@latest zod@latest
   npm i -D vitest @testing-library/react @testing-library/jest-dom
   ```
2. `next.config.ts`: set `output: 'standalone'`.
3. `lib/bq/client.ts` — BigQuery singleton using ADC:
   ```ts
   import { BigQuery } from '@google-cloud/bigquery';
   export const PROJECT = process.env.BQ_PROJECT ?? 'jarvis-bhaga-prod';
   export const DATASET = process.env.BQ_DATASET ?? 'bhaga';
   export const bq = new BigQuery({ projectId: PROJECT });
   export const fq = (name: string) => `\`${PROJECT}.${DATASET}.${name}\``;
   export async function q<T=Record<string,unknown>>(sql: string, params?: Record<string,unknown>): Promise<T[]> {
     const [rows] = await bq.query({ query: sql, params, location: 'US' });
     return rows as T[];
   }
   ```
4. `lib/auth/identity.ts` — read IAP header (server only):
   ```ts
   import { headers } from 'next/headers';
   export async function operatorEmail(): Promise<string> {
     const h = await headers();
     const raw = h.get('x-goog-authenticated-user-email') ?? '';
     const email = raw.replace(/^accounts\.google\.com:/, '');
     if (!email.endsWith('@mypalmetto.co')) throw new Error('unauthorized');
     return email;
   }
   export const DEFAULT_STORE = 'palmetto';
   ```
5. `lib/config/features.ts`:
   ```ts
   export const FEATURES = {
     sales:true, labor:true, forecast:true, orderQuality:true,
     inventory:true, payroll:true, pipeline:true,
     writeGoals:false, writeTraining:false, writeRecognition:false, writeRestock:false,
   } as const;
   ```
   (Read screens on; writes flip on in M3/M4.)
6. `app/layout.tsx` + `components/shell/*` — sidebar groups (Overview / Performance /
   People / Inventory / System) mirroring the Figma design; topbar with brand
   "Palmetto · Texas — Operator Console", store filter, pipeline-health dot.
7. `app/home/page.tsx` — minimal: render one real number from
   `vw_model_labor_daily` to prove the data path.
8. `Dockerfile` (multi-stage, standalone) + `.github/workflows/operator-console-deploy.yml`
   (build → push Artifact Registry → `gcloud run deploy`). Enable **Cloud Run direct
   IAP** and restrict to `@mypalmetto.co` (see §5.4).

**Verify (M1):**
```bash
cd apps/operator-console && npm run build          # compiles clean
npm run dev &  curl -s localhost:3000/home | grep -q "Palmetto"   # shell renders
```
Pass: build succeeds; `/home` shows the shell + one live BQ number locally (ADC).
After deploy: hitting the Cloud Run URL as a non-`@mypalmetto.co` user is blocked.

---

### M2 — Read screens (model: Sonnet)

**Goal:** all read-only screens render real view data matching Grafana.

Steps:
1. `lib/bq/queries.ts` — one typed function per view, each just parameterized
   `SELECT`. Example:
   ```ts
   import { q, fq } from './client';
   export const laborDaily = (store: string, days=30) =>
     q(`SELECT * FROM ${fq('vw_model_labor_daily')} WHERE store=@store
        AND date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
        ORDER BY date`, { store, days });
   ```
   Add: `laborWeekly`, `salesItemDaily` (`square_item_daily`), `forecast`,
   `forecastAccuracy`, `orderQualityDaily`, `kdsBySource`, `payrollPeriod`,
   `reviewBonusDetail`, `pipelineRuns` (`vw_pipeline_runs`), `sourcePulls`
   (`vw_source_pulls`), `storeConfig`.
2. Each screen is a **Server Component** calling its query fn(s) and rendering
   `components/charts/*` + `components/tables/DataTable.tsx`. Add
   `export const revalidate = 600;` (10-min cache) to each page.
3. Home: `components/kpi/HealthScorecard.tsx` — reads the daily views + goal keys
   from `store_config`; computes **status/pace in the component from already-fetched
   rows only** (comparison to a goal is presentation, not a new metric). Weekly/
   Monthly toggle switches which goal key + window.
4. `components/charts/GoalLine.tsx` — dashed ReferenceLine at the goal value.
5. Match the Figma screens (Home, Sales, Labor, Forecast, Order Quality, Pipeline
   Health).

**Verify (M2):** for one sample date, the console value equals the Grafana value:
```bash
# from repo root, compare app output vs the same view
python3 - <<'PY'
from core.datastore import read_query
print(read_query("SELECT * FROM `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` "
                 "WHERE store='palmetto' ORDER BY date DESC LIMIT 1"))
PY
```
Pass: each screen's headline numbers equal the view rows for the same store/date;
Pipeline Health freshness matches `python3 -m agents.bhaga.scripts.status --store palmetto`.

---

### M3 — Inventory + restock write-backs (model: Sonnet; Opus for parser prompt)

**Goal:** dual-date reco renders; restock schedule/actuals/reset + capacity write
through the same contracts as `cloud/webhook/handler.py`; Gemini import.

Steps:
1. Inventory read: `orderRecoCombined(store)` → `SELECT * FROM vw_order_reco_combined`;
   `nextDates(store)` → `vw_order_reco_next_dates`; `daysOfCover(store)` →
   `vw_inventory_order_assistant`.
2. Render dual-date table with `DataTable.tsx` using TanStack **column pinning**
   (`columnPinning.left = ['Item','Current Qty','Avg per day']`) — matches Grafana
   panel 83's `frozenColumns.left = 3` and the Figma design. Show `Source 1/2` as
   Estimated/Actuals badges; TOTAL row from the view's TOTAL row.
3. `lib/bq/writes.ts` — mirror the handler's exact statements (read
   `cloud/webhook/handler.py` §"Restock modal" for the canonical SQL):
   - `setRestockSchedule(store, date, by)` → MERGE `inventory_restock_schedule`
     (key store,delivery_date).
   - `replaceRestockOrders(store, date, rows, by)` → DELETE then INSERT
     `inventory_restock_orders` (replace-per-date).
   - `clearRestockOrders(store, date)` → DELETE (reset to estimated).
   - `setConfig(store, key, value)` → MERGE `store_config`.
   - `refreshOrderReco(store)` → run the 3 statements from
     `core/order_reco.py::refresh_order_reco` (DELETE, INSERT slot1, INSERT slot2).
     **Order matters: slot 1 before slot 2.**
   After any restock write or `order_reco_max_tubs` change, call `refreshOrderReco`.
4. Server actions in the drawers (`RestockImportDrawer`), guarded by
   `operatorEmail()` for `updated_by`. Nothing writes until operator confirms.
5. `app/api/parse-restock/route.ts` — accepts CSV or image:
   - CSV → parse with the same rules as `skills/inventory_parse/parse.py` and the
     handler's `_parse_restock_csv` (base,quantity; known bases only).
   - Image → Gemini vision → `{item, quantity_tubs, confidence}[]`, validate items
     against the 8 active bases, return editable rows (no BQ write here).
6. Flip `FEATURES.writeRestock = true`.

**Verify (M3):**
```bash
# after an app "Add order" for a date, the same rows appear as the Slack path
python3 - <<'PY'
from core.datastore import read_query
print(read_query("SELECT * FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders` "
                 "WHERE store='palmetto' ORDER BY delivery_date DESC, item LIMIT 20"))
PY
python3 -c "from core.order_reco import refresh_order_reco; refresh_order_reco('palmetto')"
```
Pass: app upload and `/bhaga-cloud restock` produce identical rows for a date
(replace-per-date idempotent — re-upload converges); `vw_order_reco_combined` flips
that date's Source to `Actuals`; slot 2 rechains.

---

### M4 — Remaining write-backs: goals, training, recognition (model: Sonnet)

**Goal:** Goals editor, training quick-add, recognition bonus all write to BQ.

Steps:
1. **Migration `core/migrations/033_recognition_bonuses.sql`** (idempotent):
   ```sql
   CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.recognition_bonuses` (
     store STRING NOT NULL,
     pay_period STRING NOT NULL,      -- e.g. '2026-07-01..2026-07-15'
     employee STRING NOT NULL,
     amount_cents INT64 NOT NULL,     -- integer cents (invariant)
     reason STRING,
     updated_by STRING,
     updated_at TIMESTAMP
   );
   CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_recognition_bonuses` AS
   SELECT * FROM `jarvis-bhaga-prod.bhaga.recognition_bonuses`;
   ```
   Apply via the repo's migration runner:
   `BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"`
2. `writes.ts`:
   - `upsertGoal(store, key, value, by)` → MERGE `store_config` (weekly+monthly keys
     from §2). Changing `order_reco_max_tubs` still triggers `refreshOrderReco`.
   - `addTrainingShift(store, employee, date, by)` → MERGE `training_shifts` (reuse
     handler contract; key store,employee,date).
   - `addRecognitionBonus(store, pay_period, employee, amount_cents, reason, by)` →
     MERGE `recognition_bonuses` (key store,pay_period,employee).
3. Wire `GoalsDrawer`, `TrainingQuickAdd`, `RecognitionDrawer` server actions;
   flip `FEATURES.writeGoals/writeTraining/writeRecognition = true`.
4. Home health scorecard reads goal keys (already in M2) — now editable end-to-end.

**Verify (M4):**
```bash
python3 scripts/verify.py --full        # migrations + gates green
python3 - <<'PY'
from core.datastore import read_query
print(read_query("SELECT * FROM `jarvis-bhaga-prod.bhaga.recognition_bonuses` LIMIT 5"))
print(read_query("SELECT * FROM `jarvis-bhaga-prod.bhaga.store_config` WHERE key LIKE 'goal_%'"))
PY
```
Pass: migration applies clean; each write is idempotent (re-submit → no dupes);
goals set in the drawer change the Home scorecard after refresh.

---

### M5 — Parity, evidence, cutover (model: Sonnet)

**Goal:** prove Grafana parity, finish docs, flip flags, ready to merge.

Steps:
1. Parity matrix (PLAN.md) — every Grafana panel has a console equivalent showing
   equal numbers for a sample date; capture screenshots (upload to GitHub, not local
   paths — per user-preference #18).
2. Docs lock-step: update `RUNBOOK.md` (operate/deploy the console),
   `apps/operator-console/README.md`, `PROGRESS.md` (dated entry, via PR), and run
   `python3 scripts/check_doc_freshness.py`.
3. Keep Grafana live (coexistence). Flip any remaining `FEATURES` on.
4. PR §4 evidence contract filled; `pr_cost_ledger.py validate --require-build`.

**Verify (M5):**
```bash
python3 scripts/verify.py --full
python3 scripts/check_doc_freshness.py
```
Pass: verify + doc-freshness green; parity matrix complete with hosted screenshots;
PR §4 evidence complete.

---

## 5. Cross-cutting artifacts

### 5.1 Server action write pattern (all writes look like this)
```ts
'use server';
import { operatorEmail, DEFAULT_STORE } from '@/lib/auth/identity';
import * as writes from '@/lib/bq/writes';
import { revalidatePath } from 'next/cache';
export async function addTrainingShiftAction(fd: FormData) {
  const by = await operatorEmail();                 // IAP identity or throw
  await writes.addTrainingShift(DEFAULT_STORE, String(fd.get('employee')), String(fd.get('date')), by);
  revalidatePath('/payroll');
}
```

### 5.2 Integer-cents invariant
Money is always integer cents in tables and math; format to `$` only at render
(`lib/format.ts`). Never store floats for money.

### 5.3 Timezone
All date logic uses `America/Chicago` (match the views/handler). Never use the
server's local tz.

### 5.4 Deploy workflow (`.github/workflows/operator-console-deploy.yml`)
- Trigger on push to `main` touching `apps/operator-console/**`.
- Steps: checkout → auth to GCP (WIF, same pattern as `.github/workflows/deploy.yml`)
  → build container → push to Artifact Registry → `gcloud run deploy operator-console
  --image … --region … --no-allow-unauthenticated` → enable Cloud Run IAP + bind
  `roles/iap.httpsResourceAccessor` to the `@mypalmetto.co` group only.
- Service account: least-privilege — BQ dataUser/jobUser on `bhaga`, Firestore
  viewer, Secret Manager accessor.

### 5.5 Env vars (`.env.example`)
```
BQ_PROJECT=jarvis-bhaga-prod
BQ_DATASET=bhaga
GEMINI_API_KEY=            # from Secret Manager at runtime, not committed
```

---

## 6. Global acceptance checklist (Definition of Done)

- [ ] `npm run build` clean; `vitest` unit tests pass (queries mocked).
- [ ] All 8 screens render live BQ data behind IAP (`@mypalmetto.co` only).
- [ ] Every write is idempotent and converges with the Slack path where one exists.
- [ ] Dual-date reco matches `vw_order_reco_combined` incl. Estimated/Actuals + TOTAL.
- [ ] Restock/goal/capacity writes trigger `refresh_order_reco` where required.
- [ ] Migration 033 applied; `verify.py --full` + `check_doc_freshness.py` green.
- [ ] Grafana parity matrix complete with hosted screenshots.
- [ ] No secrets, project IDs, or metrics committed; docs updated in lock-step.
```
