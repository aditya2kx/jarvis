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

Seed CSVs live under `apps/operator-console/lib/plaid/taxonomy/seed/`.
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
