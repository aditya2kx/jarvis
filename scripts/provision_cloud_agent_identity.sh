#!/usr/bin/env bash
# One-time, idempotent: lets Cursor Cloud Agents read the bot PAT from Secret Manager with no
# static secret (Cursor OIDC → GCP Workload Identity Federation), and creates the jarvis-dev
# gateway secrets. Run from an owner ADC surface (Cloud Shell / laptop):
#   bash scripts/provision_cloud_agent_identity.sh
# Trust is limited to Cloud Agents owned by the operator's Cursor user on aditya2kx/jarvis, and
# grants only secretAccessor on github-bot-pat. docs/contributing/gcp-access.md recipe F.
set -euo pipefail

P=jarvis-bhaga-prod
PN=887772634501
POOL=cursor-agents
PROVIDER=cursor
CURSOR_USER_ID=345375759
REPO=github.com/aditya2kx/jarvis
DEPLOY_SA=serviceAccount:bhaga-orchestrator@$P.iam.gserviceaccount.com
AUD="//iam.googleapis.com/projects/$PN/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"
PRINCIPALS="principalSet://iam.googleapis.com/projects/$PN/locations/global/workloadIdentityPools/$POOL/attribute.repo/$REPO"

gcloud services enable iam.googleapis.com sts.googleapis.com iamcredentials.googleapis.com secretmanager.googleapis.com --project $P

gcloud iam workload-identity-pools describe $POOL --location=global --project $P >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create $POOL --location=global --project $P \
    --display-name="Cursor Cloud Agents"

CONDITION="assertion.owner_user_id == '$CURSOR_USER_ID' && assertion.repo_url == '$REPO'"
MAPPING="google.subject=assertion.sub,attribute.repo=assertion.repo_url,attribute.owner=assertion.owner_user_id,attribute.environment=assertion.environment_id"
if gcloud iam workload-identity-pools providers describe $PROVIDER --workload-identity-pool=$POOL --location=global --project $P >/dev/null 2>&1; then
  verb=update-oidc
else
  verb=create-oidc
fi
gcloud iam workload-identity-pools providers $verb $PROVIDER --workload-identity-pool=$POOL \
  --location=global --project $P --issuer-uri=https://api.cursor.com --allowed-audiences="$AUD" \
  --attribute-mapping="$MAPPING" --attribute-condition="$CONDITION"

for s in jarvis-dev-gateway-token github-bot-pat; do
  gcloud secrets describe $s --project $P >/dev/null 2>&1 ||
    gcloud secrets create $s --replication-policy=automatic --project $P
done
if ! gcloud secrets versions list jarvis-dev-gateway-token --project $P --limit=1 --format='value(name)' | grep -q .; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' |
    gcloud secrets versions add jarvis-dev-gateway-token --data-file=- --project $P
fi

bind() { gcloud secrets add-iam-policy-binding "$1" --project $P --member="$2" --role="$3" --condition=None >/dev/null; }
bind jarvis-dev-gateway-token "$DEPLOY_SA" roles/secretmanager.secretAccessor
bind github-bot-pat "$DEPLOY_SA" roles/secretmanager.secretAccessor
bind github-bot-pat "$DEPLOY_SA" roles/secretmanager.secretVersionAdder
bind github-bot-pat "$PRINCIPALS" roles/secretmanager.secretAccessor

echo "cloud-agent identity: pool=$POOL provider=$PROVIDER aud=$AUD"
echo "cloud-agent identity: $PRINCIPALS → secretAccessor on github-bot-pat"
