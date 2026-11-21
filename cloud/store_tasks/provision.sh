#!/usr/bin/env bash
# One-time, idempotent provisioning for Store Tasks. Run from a machine with ADC
# (python3 scripts/gcp_access_probe.py → adc_ready). Safe to re-run.
set -euo pipefail

PROJECT=jarvis-bhaga-prod
REGION=us-central1
SA=store-tasks
SA_EMAIL="$SA@$PROJECT.iam.gserviceaccount.com"
DB=storetasks
BUCKET=jarvis-store-tasks-photos
TOPIC=store-tasks-events
DEPLOYER="${DEPLOYER_SA:-}"   # the GitHub Actions deploy identity; needs actAs on $SA_EMAIL

have() { "$@" >/dev/null 2>&1; }

have gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT" \
  || gcloud iam service-accounts create "$SA" --project "$PROJECT" --display-name "Store Tasks runtime"

have gcloud firestore databases describe --database "$DB" --project "$PROJECT" \
  || gcloud firestore databases create --database "$DB" --location "$REGION" --type firestore-native \
       --delete-protection --project "$PROJECT"

have gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" \
  || gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" --location "$REGION" \
       --uniform-bucket-level-access --public-access-prevention

have gcloud pubsub topics describe "$TOPIC" --project "$PROJECT" \
  || gcloud pubsub topics create "$TOPIC" --project "$PROJECT"

# Least privilege: this identity can touch only its own database, bucket and topic, and can
# read (never write) the shared schedule in the warehouse.
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA_EMAIL" \
  --role roles/datastore.user \
  --condition "title=storetasks-db-only,expression=resource.name==\"projects/$PROJECT/databases/$DB\"" >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$SA_EMAIL" \
  --role roles/storage.objectUser >/dev/null
gcloud pubsub topics add-iam-policy-binding "$TOPIC" --project "$PROJECT" \
  --member "serviceAccount:$SA_EMAIL" --role roles/pubsub.publisher >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA_EMAIL" \
  --role roles/bigquery.jobUser --condition None >/dev/null
for DATASET in bhaga bhaga_houston; do
  for TABLE in adp_scheduled_shifts adp_directory_status; do
    bq --project_id "$PROJECT" query --use_legacy_sql=false --quiet \
      "GRANT \`roles/bigquery.dataViewer\` ON TABLE \`$PROJECT.$DATASET.$TABLE\` TO 'serviceAccount:$SA_EMAIL'" \
      >/dev/null || echo "note: no $DATASET.$TABLE yet — re-run after it exists"
  done
done

for SECRET in store-tasks-signing-key store-tasks-internal-token store-tasks-push-token; do
  if ! have gcloud secrets describe "$SECRET" --project "$PROJECT"; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(48), end="")' \
      | gcloud secrets create "$SECRET" --project "$PROJECT" --replication-policy automatic --data-file=- >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT" \
    --member "serviceAccount:$SA_EMAIL" --role roles/secretmanager.secretAccessor >/dev/null
done
# Until cutover the API mirrors ClickUp (read-only); drop this binding after ClickUp is retired.
gcloud secrets add-iam-policy-binding jarvis-clickup-palmetto-pat --project "$PROJECT" \
  --member "serviceAccount:$SA_EMAIL" --role roles/secretmanager.secretAccessor >/dev/null

if [ -n "$DEPLOYER" ]; then
  gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" --project "$PROJECT" \
    --member "serviceAccount:$DEPLOYER" --role roles/iam.serviceAccountUser >/dev/null
  # The deploy workflow puts the internal token on the Cloud Scheduler job.
  gcloud secrets add-iam-policy-binding store-tasks-internal-token --project "$PROJECT" \
    --member "serviceAccount:$DEPLOYER" --role roles/secretmanager.secretAccessor >/dev/null
fi

echo "store-tasks: provisioned (sa=$SA_EMAIL db=$DB bucket=$BUCKET topic=$TOPIC)"
