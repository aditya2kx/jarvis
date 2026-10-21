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
| `/access` | Owners only — who can sign in and which pages / sections each person sees (RUNBOOK §17) |

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
Without ADC (e.g. a Cursor Cloud Agent) the shell, navigation and drawers still
render; data panels show "Data unavailable".

### Page and section access

Each page calls `pageDenied("<page>")` first and wraps sections in `can("<section>")`
(`components/access/guards.tsx`); ids live in `lib/access/registry.ts`. Adding a section:
add its id there, gate it on the page, and give any action it renders an `access` key in
`lib/actions/registry.ts` — `scripts/check_operator_console_actions.py` fails a page or
action without a check. Local dev signs in as an owner (`BYPASS_IAP_EMAIL` is added to the
owners unless `CONSOLE_OWNER_EMAILS` is set); to try a non-owner locally set
`CONSOLE_OWNER_EMAILS` and `CONSOLE_ACCESS_FILE=/tmp/access.json` (a JSON-file store instead of BigQuery).
A new section with an obvious audience may also belong in a preset (`lib/access/presets.ts`);
presets are only starting points in the drawer, never stored as such.

### Read-only preview mode

`CONSOLE_PREVIEW=1` shows a **Preview** badge in the top bar, refuses every non-GET
request in `middleware.ts` — server actions and API routes (403, toast: "Preview mode —
changes are disabled. Nothing was saved.") — and skips the two write-on-render paths
(Inventory order-reco self-heal, Accounting Plaid dedupe). Use it for review /
preview deploys and demos; never set it on the prod `operator-console` service. The one
exception is `POST /api/access` when `CONSOLE_ACCESS_TABLE` names a non-prod table (each
deployed preview gets its own sandbox grants table).

```bash
CONSOLE_PREVIEW=1 BYPASS_IAP_EMAIL=you@mypalmetto.co npm run dev
```

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
