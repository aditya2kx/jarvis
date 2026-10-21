#!/usr/bin/env bash
# Times an outside write → console cache version bump (RUNBOOK §17 "Query cache"). Owner ADC
# surface (Cloud Shell / laptop). Inserts one row into bhaga.console_cache_probe with `bq` — the
# same path as a manual fix — then polls Firestore console_cache_versions/console_cache_probe
# until the audit-log feed bumps it. Prints the version before/after and the seconds it took.
set -euo pipefail
P=jarvis-bhaga-prod
DOC="https://firestore.googleapis.com/v1/projects/$P/databases/(default)/documents/console_cache_versions/console_cache_probe"
TIMEOUT=${1:-120}

version() {
  curl -fsS -H "Authorization: Bearer $(gcloud auth print-access-token)" "$DOC" 2>/dev/null |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["fields"]["v"]["integerValue"])' 2>/dev/null || echo 0
}

before=$(version)
start=$(date +%s.%N)
bq --project_id=$P query --use_legacy_sql=false --quiet \
  "INSERT \`$P.bhaga.console_cache_probe\` (n, written_by, written_at) VALUES ($before + 1, 'console_cache_probe.sh', CURRENT_TIMESTAMP())"
committed=$(date +%s.%N)
while :; do
  now=$(version)
  if [ "$now" != "$before" ]; then
    python3 -c "import sys; s,c,e=map(float,sys.argv[1:]); print(f'probe: version {sys.argv[4]} -> {sys.argv[5]}; insert took {c-s:.1f}s; bump landed {e-c:.1f}s after commit')" \
      "$start" "$committed" "$(date +%s.%N)" "$before" "$now"
    exit 0
  fi
  if python3 -c "import sys,time; sys.exit(0 if time.time()-float(sys.argv[1]) > float(sys.argv[2]) else 1)" "$committed" "$TIMEOUT"; then
    echo "probe: no bump within ${TIMEOUT}s (version still $before)" >&2
    exit 1
  fi
  sleep 1
done
