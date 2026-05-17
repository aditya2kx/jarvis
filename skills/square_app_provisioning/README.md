# skills/square_app_provisioning

Provisions a Square Personal Access Token (PAT) for a given Jarvis store, fully via the existing browser + credentials skills. Same shape as `skills/slack_app_provisioning/`. **Reusable across every store** — Austin today, Houston in September, future stores after that.

## Why it exists

Per `jarvis.md` Hard Lesson #0 — the canonical pattern for any third-party service with a self-serve developer dashboard is `skills/<service>_app_provisioning/`. Square's developer dashboard at `developer.squareup.com/apps` is exactly this kind of UI: log in with the merchant's normal Square credentials, create an "Application", grab the Personal Access Token from the Production tab, done. No reason to ask the user to click through it.

## What it provisions (one call → token in Keychain + store profile written)

1. Drives Playwright (the `user-playwright` MCP) through `developer.squareup.com/apps`:
   - Click "+" / "Create your first application" / "Add Application"
   - Name: derived from `--store` arg (e.g. `Jarvis BHAGA Austin`)
   - Land on the new app's settings → switch to **Production** credentials tab
   - Reveal & capture the **Personal Access Token** (`EAA...` for production, `EAAA...` for sandbox)
   - Capture the **Application ID** for reference
2. Navigates to Square Dashboard → Settings → Locations to capture the **location_id** for the named store (matches `--store` against the location's name)
3. Stores the PAT in Keychain via `skills/credentials/registry.add_keychain()`:
   - Service `jarvis-square-<store>`, account `SQUARE_ACCESS_TOKEN_<STORE>`
4. Writes (or updates) `agents/bhaga/knowledge-base/store-profiles/<store>.json` with `{square_application_id, square_location_id, timezone, square_access_token_keychain_ref, ...}`
5. Sends a confirmation DM as BHAGA on Slack

## Public API

```python
from skills.square_app_provisioning import provision, register

# A) Build the playbook (no Playwright yet — pure Python)
plan = provision.build_plan(
    store="austin",
    app_name="Jarvis BHAGA Austin",
    timezone="America/Chicago",
)
# 'plan' is a list of structured browser_navigate / browser_snapshot / browser_click /
# browser_evaluate steps for the AI to drive against user-playwright.

# B) After Playwright captures token + location, finalize
register.register_store(
    store="austin",
    access_token="EAA...",
    application_id="sq0idp-...",
    location_id="L...",
    timezone="America/Chicago",
)
# Stores in Keychain, writes store-profile JSON, returns summary dict.
```

## Trust model

Square Personal Access Tokens grant **full access** to the merchant account (read + write, all endpoints). This matches the trust model the user already accepts for every other portal credential in their Keychain (Schwab, Wells Fargo, etc.) and is the standard pattern for single-tenant self-use tools. Migration to OAuth-with-`PAYMENTS_READ`-only is straightforward later (same skill, different code path) if the trust model changes — see "Future migration" below.

## Files

| File | Purpose |
|------|---------|
| `provision.py` | Playwright playbook builder for `developer.squareup.com` |
| `register.py` | Post-Playwright finalizer (Keychain + store-profile JSON + Slack confirmation) |
| `README.md` | This file |

## Multi-store reuse (the whole point)

Houston gets its own PAT in one command:

```bash
python -m skills.square_app_provisioning.provision --store houston --app-name "Jarvis BHAGA Houston"
# …drive Playwright through the plan, capture EAA + sq0idp + L tokens, then…
python -m skills.square_app_provisioning.register \
  --store houston --access-token EAA... --application-id sq0idp-... --location-id L...
```

Each store gets a separate Keychain entry (`SQUARE_ACCESS_TOKEN_HOUSTON` under `jarvis-square-houston`) and a separate store-profile JSON. BHAGA's skills (`skills/square_tips/`, `skills/tip_pool_allocation/`, etc.) take a `store` argument and resolve credentials per-store.

## Future migration to OAuth (deferred to v2)

If/when:
- BHAGA runs on a server (not just in user's Cursor session), OR
- BHAGA is shared with non-owners (other franchise operators, etc.)

…then PATs become inappropriate (full account access, single user). Migration path: add `provision_oauth.py` next to `provision.py`, scope it to `PAYMENTS_READ`, store refresh tokens instead of static PATs, add token-refresh logic to `register.py`. Same skill, different mode. The `skills/square_tips/` adapter is unaffected (it just calls `Authorization: Bearer <token>` — doesn't care if the token is a PAT or an OAuth access token).
