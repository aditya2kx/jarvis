# Palmetto Operator Console — Project Plan (living doc)

> Living plan for the Grafana-replacement website, maintained as we go. The site
> ships as **one PR** (operator preference), kept reviewable via feature-flagged
> screens.
>
> **Doc set:** [`ARCHITECTURE.md`](ARCHITECTURE.md) (tech + diagrams) ·
> [`EXECUTION.md`](EXECUTION.md) (implementation-ready, weaker-LLM steps) ·
> [`COST.md`](COST.md) (resource cost).
>
> **Milestones/execution are intentionally not filled in yet** — we align on
> architecture first, then plan milestones together.

## Objective

Replace the Grafana BHAGA Analytics dashboard with an operator-first web app that
(1) is easier to navigate, (2) frames the business against **goals** (operational
health), and (3) adds **write-backs** that today live only in Slack or nowhere:
goals, training shifts, recognition bonuses, and restock schedule/actuals.

## Scope

**In scope (v1):** Home (health command center), Sales, Labor, Forecast, Order
Quality, Payroll & People, Inventory / Ordering (dual-date Order Assistant),
Pipeline Health; goals + restock + training + recognition write-backs; IAP auth;
Cloud Run deploy; Grafana coexistence.

**Out of scope (v1):** multi-store beyond Austin (design supports it, data TBD for
Houston), mobile-native app, replacing the Slack commands (they coexist).

## Grafana parity + new-capability matrix

| Area | Grafana today | Console v1 | New vs Grafana |
|---|---|---|---|
| Sales | daily/weekly volume | Sales screen | goal lines |
| Labor | hours, labor %, hrs/item, per-person, KDS | Labor screen | goal lines, saturation read |
| Forecast | forecast + accuracy | Forecast screen | schedule table |
| Order Quality | KDS p95, by source | Order Quality screen | goal line |
| Payroll | per-employee/period, training | Payroll screen | **recognition bonus write** |
| Inventory | base analytics, **dual-date reco** | Inventory screen | **restock UI + LLM photo import** |
| Pipeline | (n/a — Grafana had no run view) | Pipeline Health | **new** |
| Goals | a few thresholds | Home health scorecard | **editable goals** |

## Design status (Figma)

All 8 screens + goals editor drawer designed. **Pending design update:** Inventory
reco → dual-date model + Estimated/Actuals + reset action; add closing-form &
restock schedule rows to Pipeline Health (per ARCHITECTURE §6). Deferred until
architecture is aligned to avoid rework.

**Canonical design reference (persist here — do not rely on chat history):**
- File: [BHAGA Operator Console — Designs](https://www.figma.com/design/Mdlm8YGTIvi6WzgLcNdaXI/BHAGA-Operator-Console-%E2%80%94-Designs?node-id=0-1)
- fileKey: `Mdlm8YGTIvi6WzgLcNdaXI` · top-level page: `0:1`
- Per-screen frame node IDs: TBD — the Figma MCP tool cannot resolve node-scoped
  calls (`get_metadata`/`get_screenshot` with a `nodeId`) from a Cursor workspace
  whose folder path exceeds macOS's practical length limit; it errors
  `ENAMETOOLONG` trying to `mkdir` its own working directory under
  `~/.cursor/projects/<workspace-name>/agent-tools`. Fix: open the branch from a
  short-path worktree (e.g. `/Users/<user>/Documents/build-workspace/i132`) —
  shallow, no-`nodeId` calls (top-level page listing) work from any path; only
  node-scoped calls need the short path.
- Screenshot-capture method for parity checks: `plugin-figma-figma-get_screenshot`
  (Figma side) + Playwright `browser_navigate` + `browser_take_screenshot` against
  the deployed Cloud Run URL (console side) — mirrors the existing Grafana capture
  method (`agents/bhaga/grafana/capture_screenshot.py`, render-API + Bearer token).

## Decisions log

| Date | Decision | Choice |
|---|---|---|
| 2026-07-02 | Auth | Google IAP, `@mypalmetto.co` |
| 2026-07-02 | Stack | Next.js (App Router) + shadcn/ui + Recharts + TanStack Table |
| 2026-07-03 | Framework version | Next.js 16 (latest at scaffold time, not the originally-noted 15 — `npm i pkg@latest` per EXECUTION.md §0); async `headers()` only, `output: 'standalone'` unaffected |
| 2026-07-02 | Hosting | Cloud Run + BQ + Secret Manager |
| 2026-07-03 | Goals storage | `store_config` (BQ) via Goals drawer |
| 2026-07-03 | Delivery | One-shot PR, feature-flagged screens |
| 2026-07-03 | Repo location | `apps/operator-console/` in this repo |
| 2026-07-03 | Recognition bonuses | New `recognition_bonuses` MERGE table (mirror `training_shifts`) |
| 2026-07-03 | LLM parsing provider | Gemini (native GCP) |
| 2026-07-03 | Goals granularity | Weekly + monthly targets per store in `store_config` |
| 2026-07-03 | BQ row serialization | `lib/bq/client.ts::q()` deep-sanitizes every row (unwraps `BigQueryDate`/`Timestamp`/`Datetime`/`Int` class instances to plain values) before returning — those class instances can't cross the Server→Client Component prop boundary, caught building M2 against live data |
| 2026-07-03 | Table cell rendering | `DataTable` columns use a serializable `meta.format` tag (`date`/`dollars`/`cents`/`pct`/`number`/`status`), never a `cell` closure — render functions built in a Server Component page also can't cross into the client `DataTable` as props |
| 2026-07-03 | BQ row sanitize discriminator | Fixed to `instanceof BigQueryDate/Datetime/Int/Time/Timestamp`, not "has a `.value` key" — the duck-typed version silently corrupted any row with a column literally named `value` (e.g. `store_config.value`) into a bare string, caught only by a live-BQ smoke test of `refreshOrderReco`'s `order_reco_max_tubs` read (surfaced as a `FLOAT64`→`INT64` TVF mismatch, root-caused one level deeper) |
| 2026-07-03 | INT64 write params | Added `intParam()` (`BigQuery.int()`) — the Node client infers plain JS numbers as `FLOAT64`; params bound against `INT64` TVF args (`tvf_order_reco_slot1/2`) must be explicitly typed |

## Milestones (execution plan)

One PR, built in verifiable phases (each behind a feature flag until parity).
Model routing per the cost playbook noted per phase.

| # | Phase | Deliverable | Verify (pass criterion) | Model |
|---|---|---|---|---|
| **M1** ✅ | Foundation | Scaffold `apps/operator-console/` (Next.js 16, Tailwind v4, shadcn); `lib/bq/` data-access; IAP identity + store scoping; app shell (sidebar/topbar/store filter); Dockerfile; Cloud Run deploy workflow | `npm run build`/`test`/`lint` clean; `/home` renders shell + attempts a real `vw_model_labor_daily` read (falls back honestly without local ADC) | Sonnet |
| **M2** ✅ | Read screens | Home (health scorecard from views + goals read), Sales, Labor, Forecast, Order Quality, Payroll & People, Pipeline Health, read-only Inventory cut | Verified: `build`/`test`/`lint` clean against **live BQ data** (local ADC); Sales/Labor cross-checked against a raw `vw_model_labor_daily` query (2026-07-02: net_sales $1,625.07, labor_pct 51.4% — matches page render); all 8 screens statically prerender with real numbers | Sonnet |
| **M3** ✅ | Inventory + restock | Dual-date reco from `vw_order_reco_combined` (frozen cols, Estimated/Actuals); restock register/add-actuals/reset + capacity edit reusing handler contracts; Gemini CSV/photo import → confirm | Verified **live against prod BQ** (not just unit): `submitRestock()` run end-to-end against a disposable far-future test date — schedule MERGE, actuals replace-converge (2nd upload replaces, doesn't accumulate), `refreshOrderReco` recompute, cleanup confirmed empty; `build`/`test`/`lint` clean | Sonnet + Opus (parse) |
| **M4** ✅ | Write-backs | Goals editor → `store_config` (weekly+monthly); training quick-add → `training_shifts`; recognition bonus → new `recognition_bonuses` table (migration `033_recognition_bonuses.sql`, applied) | Verified **live against prod BQ**: migration applied clean (`ensure_schema()` → `['033_recognition_bonuses']`); all 3 writes (`upsertGoal`, `addTrainingShift`, `addRecognitionBonus`) run end-to-end against disposable test rows — MERGE converges on re-submit, no dupes, cleanup confirmed empty; `build`/`test`/`lint` clean | Sonnet |
| **M5** ✅ | Parity + cutover | Grafana coexistence check, evidence capture, docs (RUNBOOK/README/PROGRESS), flip feature flags | All 8 screens confirmed rendering live prod data in one dev-server session (below); docs updated; `verify.py --full` invoked | Sonnet |

### M5 live-render evidence (2026-07-03, `npm run dev` + `BYPASS_IAP_EMAIL`, prod BQ)

All 8 screens confirmed rendering with live figures in one session (dev server started,
curled, killed — no screenshots taken: the sandboxed browser tooling can't reach `localhost`
in this worktree, and this workspace's path is too long for the terminal-backgrounding
mechanism to keep a dev server alive across tool calls, so live HTML output is the evidence):

| Screen | Sample rendered figures |
|---|---|
| Home | Weekly: Net sales $15,026.25, Labor % 33.2%; Monthly: Net sales $59,844.64, Labor % 36.3%; goal status `no-goal` (expected — no goals set yet) |
| Sales | Daily net sales incl. $1,625.07, $1,595.02, $1,800.47 |
| Labor | $1,625.07 net sales / $835.11 labor cost / 51.4% labor — matches the raw `vw_model_labor_daily` cross-check recorded in M2 |
| Forecast | Forecast rows rendering (1.52, 2.58, 7.66, 8.95, 6.24) |
| Order Quality | KDS p95 columns rendering |
| Payroll | Est. wages $9,467.77, Review bonuses $80.00, Wage diff $1,713.65; Training shifts + Recognition bonuses sections present (empty — no rows exist for either yet) |
| Inventory | Dual-date reco with real dates (On hand 2026-07-16, 2026-07-23), Source badge "Estimated" |
| Pipeline | Latest run status "success" |

### Migration applied (M4)

`recognition_bonuses` — key `(store, pay_period, employee)`; columns `amount_cents`
(integer cents), `reason`, `updated_by`, `updated_at`; reconciled against the ADP
bonus earnings line. Mirrors `training_shifts` MERGE semantics. DDL in
[`core/migrations/033_recognition_bonuses.sql`](../../core/migrations/033_recognition_bonuses.sql),
applied to `jarvis-bhaga-prod.bhaga` 2026-07-03.

> **Step-by-step implementation** (exact files, commands, DDL, per-step verify) is
> in [`EXECUTION.md`](EXECUTION.md) — written for a weaker/cheaper model to execute.
