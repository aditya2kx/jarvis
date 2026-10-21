#!/usr/bin/env bash
# One-time, idempotent: gives Cursor Cloud Agents a GCP identity with no static secret (Cursor
# OIDC → Workload Identity Federation → service account cursor-cloud-agent), so they can read the
# bot PAT and open Operator Console previews with an ID token; also creates the jarvis-dev
# gateway secrets. Run from an owner ADC surface (Cloud Shell / laptop):
#   bash scripts/provision_cloud_agent_identity.sh
# Trust is limited to Cloud Agents owned by the operator's Cursor user on aditya2kx/jarvis. The SA
# holds secretAccessor on github-bot-pat and slack-bot-token (operator_ping.py) only; previews add it to their IAP list
# (operator-console-preview.yml) — never prod. docs/contributing/gcp-access.md recipe F.
set -euo pipefail

P=jarvis-bhaga-prod
PN=887772634501
POOL=cursor-agents
PROVIDER=cursor
CURSOR_USER_ID=345375759
REPO=github.com/aditya2kx/jarvis
DEPLOY_SA=serviceAccount:bhaga-orchestrator@$P.iam.gserviceaccount.com
AGENT_SA_NAME=cursor-cloud-agent
AGENT_SA=$AGENT_SA_NAME@$P.iam.gserviceaccount.com
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

gcloud iam service-accounts describe $AGENT_SA --project $P >/dev/null 2>&1 ||
  gcloud iam service-accounts create $AGENT_SA_NAME --project $P \
    --display-name="Cursor Cloud Agents (no roles; per-resource grants only)"
gcloud iam service-accounts add-iam-policy-binding $AGENT_SA --project $P \
  --member="$PRINCIPALS" --role=roles/iam.workloadIdentityUser >/dev/null

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
bind github-bot-pat "serviceAccount:$AGENT_SA" roles/secretmanager.secretAccessor
# scripts/operator_ping.py: Slack DM the operator for a 2-Step tap / code / click (Hard Lesson #7).
bind slack-bot-token "serviceAccount:$AGENT_SA" roles/secretmanager.secretAccessor

echo "cloud-agent identity: pool=$POOL provider=$PROVIDER aud=$AUD"
echo "cloud-agent identity: $PRINCIPALS → $AGENT_SA (secretAccessor on github-bot-pat)"
