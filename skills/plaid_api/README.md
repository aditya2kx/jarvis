# Plaid API skill (Issue #158)

Thin urllib client for Plaid Link + `/transactions/sync`. Used by the Operator
Console Accounting page and `bhaga-webhook` `/plaid/webhook` + `/plaid/sync`.

## Secrets

| Name | Where | Notes |
|------|-------|-------|
| `plaid_client_id` / env `PLAID_CLIENT_ID` | Secret Manager or Cloud Run env | Dashboard |
| `plaid_secret` / env `PLAID_SECRET` | Secret Manager or Cloud Run env | sandbox or production |
| `plaid_access_token_<item_id>` | Secret Manager / Keychain | Per linked Item; never in BQ |
| `PLAID_ENV` | env | `sandbox` \| `development` \| `production` |

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

## Files

| File | Role |
|------|------|
| `auth.py` | client_id/secret + per-item access_token |
| `client.py` | link/token, exchange, transactions/sync |
| `sync.py` | cursor drain → BQ MERGE/DELETE |
