# Palmetto Operator Console

Next.js app replacing the Grafana Bhaga Analytics dashboard with a
navigable, write-back-capable console for the Austin store operator.

Full design/decisions/build plan: [`docs/operator-console/`](../../docs/operator-console/)
(`ARCHITECTURE.md`, `PLAN.md`, `EXECUTION.md`, `COST.md`).

## Screens (Issue #158)

| Route | Purpose |
|---|---|
| `/home` | **Goal and Tracking** — net sales, PT + total labor %, prep p95, bases at risk |
| `/accounting` | Linked bank feed (money in/out/cash flow + Palmetto categories) with Square net sales context and $ / % toggle |
| `/sales` `/labor` `/order-quality` | Performance drill-downs (Forecast console page removed — Issue #213) |
| `/inventory` | Ordering + Base runway |
| `/payroll` `/pipeline` `/automations` | People + system + scheduled automations (Team pulse) |

Plaid skill: [`skills/plaid_api/`](../../skills/plaid_api/README.md). Migration `037_plaid_transactions.sql`.

## Local development

```bash
cd apps/operator-console
npm install
cp .env.example .env.local   # set BYPASS_IAP_EMAIL for local auth
# Optional Plaid (Cloud Run uses PLAID_ENV=production after Issue #168):
# PLAID_CLIENT_ID=... PLAID_SECRET=... PLAID_ENV=production
npm run dev
```

Requires Application Default Credentials for BigQuery reads
(`gcloud auth application-default login`, project `jarvis-bhaga-prod`).

## Commands

| Command | Purpose |
|---|---|
| `npm run dev` | Local dev server (Turbopack) |
| `npm run build` | Production build (`output: 'standalone'`) |
| `npm test` | Vitest unit tests |
| `npm run lint` | ESLint |

## Deploy

Pushes to `main` touching this directory trigger
[`.github/workflows/operator-console-deploy.yml`](../../.github/workflows/operator-console-deploy.yml)
— builds the container, applies pending BQ migrations, and deploys to Cloud
Run behind direct IAP with `--min-instances=1` (Issue #175). See `docs/operator-console/COST.md`
for the cost model and `RUNBOOK.md` §17 for operating the deployed console.

Mutating UI: every write goes through `lib/actions/useConsoleAction` + `ActionAck`
(see `lib/actions/MUTATING_ACTIONS.md`). Heavy recomputes enqueue Cloud Run Jobs.
