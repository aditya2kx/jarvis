# vault — per-site credentials for agents (1Password)

Ad-hoc development logins (portals with no API, Cloud Shell sign-in, vendor dashboards) come from
one 1Password vault, so a Cursor Cloud Agent can sign in by itself the way the Mac agent used
Keychain + `user-playwright`.

| | |
|---|---|
| Store | 1Password vault **`Shared with Bots`** (override `JARVIS_VAULT`) — one **Login** item per site: username, password, website URL, optional one-time password |
| Agent access | 1Password **service account** with `read_items` on that vault only → `OP_SERVICE_ACCOUNT_TOKEN` (Cursor dashboard → Cloud Agents → Secrets, type **Runtime Secret**, scoped to `aditya2kx/jarvis`) |
| CLI | `op` 2.40.0, installed by `.cursor/environment.json` |
| Limits | personal plans: 1,000 reads/hour per token, 1,000 requests/day per account — each command here is 2–4 reads |

## Use

```bash
python3 -m skills.vault list                                   # titles + sites, no secrets
python3 -m skills.vault get accounts.google.com                # username only
# click the field in the browser first, then:
python3 -m skills.vault type accounts.google.com --field password --enter
python3 -m skills.vault type accounts.google.com --field otp --enter
# scripted logins: secret only in the child's environment
python3 -m skills.vault run adp --field password --as ADP_PASSWORD -- python3 login.py
```

A site resolves by item title, then by website host (exact, then parent domain).

## Never

- print a password or code: `get` returns the username only; `type` sends the value to xdotool
  stdin; `run` passes it in the child environment;
- give the service account write access or a second vault;
- store GCP / GitHub machine credentials here — those use OIDC federation
  (`docs/contributing/gcp-access.md` recipe F).

## Operator one-time setup

1. 1Password vault **Shared with Bots** (exists): one Login item per site the agents may use.
2. 1Password.com → Developer → Directory → **Service account** → access: `Shared with Bots`, *Read items*
   only (or `op service-account create jarvis-cloud-agents --vault "Shared with Bots:read_items"`).
3. Cursor dashboard → Cloud Agents → Secrets → `OP_SERVICE_ACCOUNT_TOKEN` = the token
   (Runtime Secret, repo `aditya2kx/jarvis`). New agents pick it up.

Cloud Shell runs as the signed-in Google account, so GCP provisioning (e.g.
`scripts/provision_cloud_agent_identity.sh`) needs the project owner's item — `Google (aditya.2ky)`,
username `aditya.2ky@gmail.com`; `adi@mypalmetto.co` has no IAM on `jarvis-bhaga-prod`. Two items
share `accounts.google.com`, so address them by title.

When a sign-in still needs the operator (2-Step "Tap Yes", an SMS code, a click), DM them and wait —
never stop and report the blocker in chat (Hard Lesson #7):

```bash
python3 scripts/operator_ping.py "Google 2-Step for Cloud Shell: tap Yes on your phone"
python3 scripts/operator_ping.py "Reply with the Google SMS code" --wait 600 --type --enter   # field focused first
```

Email codes: read the inbox instead. For a Google sign-in, add the account's authenticator-app code to its item (Google account →
Security → 2-Step Verification → Authenticator → scan with 1Password) so the agent can answer
"Try another way → Google Authenticator" without your phone.
