# GCP access — surfaces, recipes, Cloud Agent gap

**Canonical for any Jarvis agent that needs `gcloud`, Secret Manager writes, Cloud Run
mutations, or ADC.** [RUNBOOK.md](../../RUNBOOK.md) §7 lists secret *names*; this file answers
*who can talk to GCP from where*.

Incident that forced this doc (2026-08-24): a Cursor Cloud Agent tried to seed
`tesla-aladdin-garage` secrets in `jarvis-bhaga-prod`. The VM had **no `gcloud` binary
and no Application Default Credentials**. User-preference 20 (“do the GCP ops yourself”)
still holds — it applies to surfaces that *have* identity, not to inventing a JSON
service-account key on a Cloud Agent.

```bash
python3 scripts/gcp_access_probe.py
```

Run that **before** any Secret Manager write or `gcloud` command. The printed
`surface=` line picks the recipe below.

---

## Surfaces (what you actually have)

| Surface | How you got here | Identity | Typical tools |
|---|---|---|---|
| **GitHub Actions** | `deploy.yml`, `tesla-aladdin-garage-deploy.yml`, cost/BQ workflows | Workload Identity Federation | `google-github-actions/auth` + `gcloud` in the job |
| **Laptop / Cloud Shell** | operator machine, `gcloud auth application-default login` | User ADC (`aditya.2ky@gmail.com`) | `hydrate`, `secret_manager_put.py`, optional `gcloud` CLI |
| **Cloud Run / GCE** | runtime of `bhaga-webhook`, `tesla-aladdin-garage`, jobs | Attached service account | Google client libraries; no laptop Keychain |
| **Cursor Cloud Agent** | `CURSOR_AGENT=1` VM (iPhone / desktop cloud run) | **None by default** | `gh` (often read-only), no ADC, often no `gcloud` |

Repo secrets used by WIF (names only — values never in git):

- `WIF_PROVIDER`
- `WIF_SERVICE_ACCOUNT` → `bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com`

WIF is **CI-only**. A Cloud Agent cannot impersonate that pool from the VM. `gh secret` is
also read-only in this Cloud Agent environment.

---

## Recipes

### A — Read secrets into local Keychain (laptop, ADC)

No `gcloud` binary required:

```bash
python3 -m skills.credentials.registry audit
python3 -m skills.credentials.registry hydrate-all
```

`hydrate` uses `google.cloud.secretmanager` + ADC. It never prints values.

### B — Write / rotate a Secret Manager version (laptop or Cloud Shell)

Prefer the ADC script (same libraries as `provision_sandbox_token.py`) so agents
do not depend on the `gcloud` CLI being installed:

```bash
# From a gitignored file (one secret per invocation)
python3 scripts/secret_manager_put.py \
  --secret tesla-fleet-client-id \
  --data-file /path/to/value.txt

# From an already-exported env var (value is not printed)
python3 scripts/secret_manager_put.py \
  --secret tesla-fleet-refresh-token \
  --from-env TESLA_REFRESH_TOKEN
```

Equivalent CLI (if `gcloud` *and* ADC / `gcloud auth` are present):

```bash
printf '%s' "$TESLA_CLIENT_ID" | gcloud secrets versions add tesla-fleet-client-id \
  --data-file=- --project jarvis-bhaga-prod
```

Never commit the file. Never paste secret values into a PR, chat, or GitHub Actions
`workflow_dispatch` input (those land in logs).

### C — Deploy / mutate Cloud Run (CI)

After merge to `main`, GitHub Actions authenticates with WIF and runs `gcloud`.
Do not deploy prod from a Cloud Agent VM. Do not mint a JSON key to “unblock” the agent.

### D — Cursor Cloud Agent (`surface=cursor_cloud_no_adc`)

This is expected, not a broken install.

**Do:**

1. Probe (`gcp_access_probe.py`) and cite `surface=cursor_cloud_no_adc` in the status line.
2. Keep secret *values* out of git; gitignored `local/` is fine on the agent disk.
3. Finish code + workflow + RUNBOOK in the PR.
4. Put Secret Manager versions from **recipe B** on a laptop/Cloud Shell that already
   has ADC.

**Do not:**

- Ask the operator to click through Cloud Console for steps an ADC shell can run.
- Store a GCP service-account JSON key in Cursor environment secrets (no static keys; WIF only).
- Install `gcloud` on the Cloud Agent and expect it to work — without ADC, `gcloud`
  still cannot call `jarvis-bhaga-prod`.
- Run a second copy of `tesla-aladdin-garage` on the agent “until Cloud Run is up”.

Grafana **dev-cost** panel queries go through Grafana’s Bearer token, **not** ADC — see
`grafana/jarvis_dev/` and `agents/bhaga/grafana/README.md` (BHAGA Analytics UI retired).

### E — One-time laptop ADC login

```bash
gcloud auth application-default login   # aditya.2ky@gmail.com
gcloud config set project jarvis-bhaga-prod
python3 scripts/gcp_access_probe.py     # expect surface=adc_ready
```

---

## Tesla Aladdin garage seed list

Secret Manager ids (must match `tesla-aladdin-garage-deploy.yml` `--set-secrets`):

| Secret id | Env on Cloud Run |
|---|---|
| `tesla-fleet-client-id` | `TESLA_CLIENT_ID` |
| `tesla-fleet-client-secret` | `TESLA_CLIENT_SECRET` |
| `tesla-fleet-refresh-token` | `TESLA_REFRESH_TOKEN` (may be empty until `/oauth/tesla`) |
| `aladdin-connect-username` | `ALADDIN_USERNAME` |
| `aladdin-connect-password` | `ALADDIN_PASSWORD` |

Refresh token is obtained after the service URL exists and the Tesla app redirect
includes `https://<service>/oauth/tesla`. Client id/secret/Aladdin can be put first.

---

## Experience checklist (agent)

- [ ] Ran `python3 scripts/gcp_access_probe.py` and recorded `surface=`.
- [ ] Did not claim “Jarvis has gcloud” without a passing probe.
- [ ] Writes used recipe B or C, never a JSON key.
- [ ] Cloud Agent: PR + docs done; secret put deferred to ADC, not Console click-ops.
