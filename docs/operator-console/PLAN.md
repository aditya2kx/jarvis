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

## Milestones (execution plan)

One PR, built in verifiable phases (each behind a feature flag until parity).
Model routing per the cost playbook noted per phase.

| # | Phase | Deliverable | Verify (pass criterion) | Model |
|---|---|---|---|---|
| **M1** ✅ | Foundation | Scaffold `apps/operator-console/` (Next.js 16, Tailwind v4, shadcn); `lib/bq/` data-access; IAP identity + store scoping; app shell (sidebar/topbar/store filter); Dockerfile; Cloud Run deploy workflow | `npm run build`/`test`/`lint` clean; `/home` renders shell + attempts a real `vw_model_labor_daily` read (falls back honestly without local ADC) | Sonnet |
| **M2** ✅ | Read screens | Home (health scorecard from views + goals read), Sales, Labor, Forecast, Order Quality, Payroll & People, Pipeline Health, read-only Inventory cut | Verified: `build`/`test`/`lint` clean against **live BQ data** (local ADC); Sales/Labor cross-checked against a raw `vw_model_labor_daily` query (2026-07-02: net_sales $1,625.07, labor_pct 51.4% — matches page render); all 8 screens statically prerender with real numbers | Sonnet |
| **M3** | Inventory + restock | Dual-date reco from `vw_order_reco_combined` (frozen cols, Estimated/Actuals); restock register/add-actuals/reset + capacity edit reusing handler contracts; Gemini CSV/photo import → confirm | App restock writes converge with `/bhaga-cloud restock`; `refresh_order_reco` recomputes; idempotent re-upload | Sonnet + Opus (parse) |
| **M4** | Write-backs | Goals editor → `store_config` (weekly+monthly); training quick-add → `training_shifts`; recognition bonus → new `recognition_bonuses` table (migration + reconciliation) | Writes idempotent, reflected on refresh; migration applies clean; `verify.py --full` green | Sonnet |
| **M5** | Parity + cutover | Grafana coexistence check, evidence screenshots, docs (RUNBOOK/README/PROGRESS), flip feature flags | Parity matrix green; docs fresh; PR §4 evidence complete | Sonnet |

### New migration required (M4)

`recognition_bonuses` — key `(store, pay_period, employee)`; columns `amount_cents`
(integer cents), `reason`, `updated_by`, `updated_at`; reconciled against the ADP
bonus earnings line. Mirrors `training_shifts` MERGE semantics. Full DDL +
step-by-step in [`EXECUTION.md`](EXECUTION.md) §M4.

> **Step-by-step implementation** (exact files, commands, DDL, per-step verify) is
> in [`EXECUTION.md`](EXECUTION.md) — written for a weaker/cheaper model to execute.
