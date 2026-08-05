# Plaid API skill (Issue #158)

Thin urllib client for Plaid Link + `/transactions/sync`. Used by the Operator
Console Accounting page and `bhaga-webhook` `/plaid/webhook` + `/plaid/sync`.

## Secrets

| Name | Where | Notes |
|------|-------|-------|
| `plaid_client_id` / env `PLAID_CLIENT_ID` | Secret Manager or Cloud Run env | Dashboard |
| `plaid_secret` / env `PLAID_SECRET` | Secret Manager or Cloud Run env | sandbox or production |
| `plaid_access_token_<item_id>` | Secret Manager / Keychain | Per linked Item; never in BQ |
| `PLAID_ENV` | env | `sandbox` \| `development` \| `production` (Cloud Run uses `production` after Issue #168) |

```bash
# Laptop hydrate example (after creating SM secrets):
BHAGA_SECRETS_BACKEND=gcp python3 -m skills.credentials.registry hydrate plaid_client_id
```

## CLI smoke

```bash
PLAID_ENV=sandbox python3 -c "
from skills.plaid_api.client import PlaidClient
c = PlaidClient()
print(c.link_token_create(client_user_id='smoke')['link_token'][:20])
"
```

## Sync

```bash
python3 -c "
from skills.plaid_api.sync import sync_item
print(sync_item('palmetto', '<item_id>'))
"
```

## Webhook URL (Issue #220)

Link only registers a webhook when console has `PLAID_WEBHOOK_URL` set (prod deploy
points at `bhaga-webhook` `/plaid/webhook`). For an existing Item with an empty
webhook field:

```bash
PLAID_ENV=production BHAGA_SECRETS_BACKEND=gcp python3 -c "
from skills.plaid_api.sync import update_item_webhook
print(update_item_webhook(
    'palmetto', '<item_id>',
    'https://bhaga-webhook-4yl5izovxq-uc.a.run.app/plaid/webhook',
))
"
```

Nightly catch-up: `daily_refresh._plaid_sync_linked_items` (best-effort, non-fatal)
on the existing `bhaga-nightly` cron — no dedicated Plaid Cloud Scheduler.
Manual **Sync now** on `/accounting` remains the backfill / last-resort path.

## Taxonomy seed + reapply (Issue #160)

```bash
BHAGA_DATASTORE=bigquery python3 -c "
from skills.plaid_api.taxonomy_seed import seed_taxonomy, extend_corpus_rules
print(seed_taxonomy(dry_run=False))
print(extend_corpus_rules(dry_run=False))
"
BHAGA_DATASTORE=bigquery python3 -c "
from google.cloud import bigquery
from skills.plaid_api.categorize import reapply_categories
print(reapply_categories(bigquery.Client(project='jarvis-bhaga-prod')))
"
```

Live merchant/brand seed CSVs are **not in git** — set `PLAID_TAXONOMY_SEED_DIR`
(or use gitignored `local/plaid-taxonomy-seed/`). See
`apps/operator-console/lib/plaid/taxonomy/seed/README.md`.
`sync_item` categorizes upserted rows after suggestInternal (never clears overrides).


## Purge Item (sandbox retirement)

```bash
BHAGA_DATASTORE=bigquery python3 -c "
from skills.plaid_api.sync import purge_item
print(purge_item('palmetto', '<item_id>', dry_run=True))   # counts only
print(purge_item('palmetto', '<item_id>', dry_run=False))  # DELETE txns then item
"
```

## Files

| File | Role |
|------|------|
| `auth.py` | client_id/secret + per-item access_token |
| `client.py` | link/token, exchange, transactions/sync |
| `sync.py` | cursor drain → BQ MERGE/DELETE; categorize upserts; `purge_item` |
| `category_rules.py` | Pure rule match (priority / amount_sign) |
| `categorize.py` | Load rules from BQ + reapply to txns |
| `taxonomy_seed.py` | Copilot CSV → taxonomy nodes + rules |
