#!/usr/bin/env bash
# Idempotent: wires the Operator Console query cache's change feed (ARCHITECTURE.md §15,
# RUNBOOK §17 "Query cache"). Run from an owner ADC surface (Cloud Shell / laptop):
#
#   bash scripts/provision_console_cache.sh sandbox   # pre-merge: private consumer built from this checkout
#   bash scripts/provision_console_cache.sh prod      # post-merge: point the feed at bhaga-webhook
#
# BigQuery audit log (every write to the `bhaga` dataset, whoever makes it) → log sink →
# Pub/Sub topic → push (OIDC as console-cache-push) → <consumer>/cache/bq-write → Firestore
# console_cache_versions/{table} += 1. `prod` also deletes the sandbox consumer, then sets
# CONSOLE_QUERY_CACHE=on after a probe bump (cache is on by default in code; this undoes any =off).
set -euo pipefail

MODE=${1:?usage: provision_console_cache.sh sandbox|prod}
P=jarvis-bhaga-prod
PN=887772634501
REGION=us-central1
DATASET=bhaga
TOPIC=bhaga-bq-writes
SINK=bhaga-bq-writes
SUB=console-cache-versions
PUSH_SA_NAME=console-cache-push
PUSH_SA=$PUSH_SA_NAME@$P.iam.gserviceaccount.com
CONSOLE_SA=$PN-compute@developer.gserviceaccount.com
CI_SA=bhaga-orchestrator@$P.iam.gserviceaccount.com
SANDBOX_SVC=bhaga-webhook-cachepv
REGISTRY=$REGION-docker.pkg.dev/$P/jarvis-images

gcloud services enable pubsub.googleapis.com logging.googleapis.com firestore.googleapis.com cloudbuild.googleapis.com --project $P

# Probe table for the outside-write check (core/migrations/091_console_cache_probe.sql).
bq --project_id=$P query --use_legacy_sql=false --quiet \
  "CREATE TABLE IF NOT EXISTS \`$P.$DATASET.console_cache_probe\` (n INT64 NOT NULL, written_by STRING NOT NULL, written_at TIMESTAMP NOT NULL)"

gcloud pubsub topics describe $TOPIC --project $P >/dev/null 2>&1 ||
  gcloud pubsub topics create $TOPIC --project $P --message-retention-duration=1d

FILTER="protoPayload.metadata.\"@type\"=\"type.googleapis.com/google.cloud.audit.BigQueryAuditMetadata\"
protoPayload.resourceName:\"projects/$P/datasets/$DATASET/\"
(protoPayload.metadata.tableDataChange:* OR protoPayload.metadata.tableCreation:* OR protoPayload.metadata.tableChange:* OR protoPayload.metadata.tableDeletion:* OR protoPayload.metadata.routineCreation:* OR protoPayload.metadata.routineChange:* OR protoPayload.metadata.routineDeletion:*)"
if gcloud logging sinks describe $SINK --project $P >/dev/null 2>&1; then
  gcloud logging sinks update $SINK pubsub.googleapis.com/projects/$P/topics/$TOPIC --project $P --log-filter="$FILTER" --quiet
else
  gcloud logging sinks create $SINK pubsub.googleapis.com/projects/$P/topics/$TOPIC --project $P --log-filter="$FILTER" --quiet
fi
WRITER=$(gcloud logging sinks describe $SINK --project $P --format='value(writerIdentity)')
gcloud pubsub topics add-iam-policy-binding $TOPIC --project $P --member="$WRITER" --role=roles/pubsub.publisher >/dev/null

gcloud iam service-accounts describe $PUSH_SA --project $P >/dev/null 2>&1 ||
  gcloud iam service-accounts create $PUSH_SA_NAME --project $P \
    --display-name="Pub/Sub push identity for console cache versions (no roles)"
gcloud iam service-accounts add-iam-policy-binding $PUSH_SA --project $P \
  --member="serviceAccount:service-$PN@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountTokenCreator >/dev/null

# Cached results expire on their own; versions are bumped, never deleted.
gcloud firestore fields ttls update expire_at --collection-group=console_query_cache_entries \
  --enable-ttl --project $P --quiet >/dev/null
gcloud projects add-iam-policy-binding $P --member="serviceAccount:$CONSOLE_SA" \
  --role=roles/datastore.user --condition=None >/dev/null

WEBHOOK_SA=$(gcloud run services describe bhaga-webhook --region $REGION --project $P \
  --format='value(spec.template.spec.serviceAccountName)')

case "$MODE" in
  sandbox)
    # Same runtime SA as bhaga-webhook (Firestore access) but no Slack/Plaid config and no
    # public ingress: only the push SA may invoke it. Prod bhaga-webhook is untouched.
    # Cloud Build, not Cloud Shell's docker: its pushes to Artifact Registry get "connection refused".
    TAG=$REGISTRY/bhaga-webhook:cachepv-$(git rev-parse --short HEAD)
    BUILD=$(mktemp)
    printf 'steps:\n- name: gcr.io/cloud-builders/docker\n  args: [build, -t, "%s", -f, cloud/webhook/Dockerfile, .]\nimages: ["%s"]\n' "$TAG" "$TAG" > "$BUILD"
    gcloud builds submit . --project $P --region $REGION --config "$BUILD" --quiet
    gcloud run deploy $SANDBOX_SVC --image "$TAG" --region $REGION --project $P \
      --service-account "$WEBHOOK_SA" --no-allow-unauthenticated --max-instances 1 --quiet
    URL=$(gcloud run services describe $SANDBOX_SVC --region $REGION --project $P --format='value(status.url)')
    gcloud run services update $SANDBOX_SVC --region $REGION --project $P --quiet \
      --update-env-vars "CACHE_PUSH_AUDIENCE=$URL,CACHE_PUSH_SA=$PUSH_SA"
    gcloud run services add-iam-policy-binding $SANDBOX_SVC --region $REGION --project $P \
      --member="serviceAccount:$PUSH_SA" --role=roles/run.invoker >/dev/null
    ;;
  prod)
    URL=$(gcloud run services describe bhaga-webhook --region $REGION --project $P --format='value(status.url)')
    gcloud run services update bhaga-webhook --region $REGION --project $P --quiet \
      --update-env-vars "CACHE_PUSH_AUDIENCE=$URL,CACHE_PUSH_SA=$PUSH_SA"
    ;;
  *) echo "unknown mode $MODE" >&2; exit 2 ;;
esac

ENDPOINT=$URL/cache/bq-write
if gcloud pubsub subscriptions describe $SUB --project $P >/dev/null 2>&1; then
  gcloud pubsub subscriptions update $SUB --project $P --push-endpoint="$ENDPOINT" \
    --push-auth-service-account=$PUSH_SA --push-auth-token-audience="$URL"
else
  gcloud pubsub subscriptions create $SUB --project $P --topic=$TOPIC --push-endpoint="$ENDPOINT" \
    --push-auth-service-account=$PUSH_SA --push-auth-token-audience="$URL" \
    --ack-deadline=30 --min-retry-delay=5s --max-retry-delay=60s
fi
# operator-console-preview.yml turns a preview's cache on only if CI can see this subscription.
gcloud pubsub subscriptions add-iam-policy-binding $SUB --project $P \
  --member="serviceAccount:$CI_SA" --role=roles/pubsub.viewer >/dev/null

if [ "$MODE" = prod ] && gcloud run services describe $SANDBOX_SVC --region $REGION --project $P >/dev/null 2>&1; then
  gcloud run services delete $SANDBOX_SVC --region $REGION --project $P --quiet
fi

echo "console cache feed: sink=$SINK → topic=$TOPIC → sub=$SUB → $ENDPOINT (push as $PUSH_SA)"

# Cache is on by default in code; still set CONSOLE_QUERY_CACHE=on after a probe bump so any
# leftover =off is cleared and the enable step is recorded in the Cloud Shell log.
if [ "$MODE" = prod ]; then
  bash "$(dirname "$0")/console_cache_probe.sh" 180
  gcloud run services update operator-console --region $REGION --project $P --quiet \
    --update-env-vars CONSOLE_QUERY_CACHE=on
  echo "console cache: CONSOLE_QUERY_CACHE=on for operator-console"
fi
