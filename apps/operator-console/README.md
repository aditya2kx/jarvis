# Palmetto Operator Console

Next.js app replacing the Grafana Bhaga Analytics dashboard with a
navigable, write-back-capable console for the Austin store operator.

Full design/decisions/build plan: [`docs/operator-console/`](../../docs/operator-console/)
(`ARCHITECTURE.md`, `PLAN.md`, `EXECUTION.md`, `COST.md`).

## Local development

```bash
cd apps/operator-console
npm install
cp .env.example .env.local   # set BYPASS_IAP_EMAIL for local auth
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
Run behind direct IAP (`@mypalmetto.co` only). See `docs/operator-console/COST.md`
for the cost model and `RUNBOOK.md` for operating the deployed console.
