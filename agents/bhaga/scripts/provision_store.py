#!/usr/bin/env python3
"""One-time GCP provisioning for an additional BHAGA store (Issue #533).

Each store runs the same ``bhaga-daily-refresh`` job against its own targets,
named in ``store-profiles/<store>.json``: BQ dataset ``bq_dataset``, Firestore
collection ``firestore_collection`` (created on first write) and scrape-cache
bucket ``gcs_cache_bucket``. This creates the dataset (all migrations), the
bucket (same access as Austin's) and the store's nightly Cloud Scheduler job.
Idempotent; run from an ADC surface (``python3 scripts/gcp_access_probe.py``).

    python3 -m agents.bhaga.scripts.provision_store --store houston --dry-run
    python3 -m agents.bhaga.scripts.provision_store --store houston --schedule "30 22 * * *" --paused
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

PROJECT = "jarvis-bhaga-prod"
REGION = "us-central1"
JOB = "bhaga-daily-refresh"
SCHEDULER_SA = "bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com"
BUCKET_ACCESS = (
    ("serviceAccount:bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com",
     "roles/storage.objectUser"),
    ("serviceAccount:cursor-cloud-agent@jarvis-bhaga-prod.iam.gserviceaccount.com",
     "roles/storage.objectViewer"),
)
PROFILES = pathlib.Path(__file__).resolve().parents[1] / "knowledge-base" / "store-profiles"


def store_env(profile: dict) -> dict[str, str]:
    """Job env overrides that point one execution at ``profile``'s store."""
    return {
        "BHAGA_STORE": profile["store_id"],
        "BHAGA_BQ_DATASET": profile["bq_dataset"],
        "BHAGA_FIRESTORE_COLLECTION": profile["firestore_collection"],
        "BHAGA_GCS_CACHE_BUCKET": profile["gcs_cache_bucket"],
    }


def run_request_body(profile: dict) -> dict:
    """``jobs:run`` body for the store's nightly: same image, store args + env."""
    return {"overrides": {"containerOverrides": [{
        "args": ["-m", "agents.bhaga.scripts.daily_refresh", "--store", profile["store_id"]],
        "env": [{"name": k, "value": v} for k, v in store_env(profile).items()],
    }]}}


def _sh(cmd: list[str], *, dry_run: bool, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _exists(cmd: list[str]) -> bool:
    return subprocess.run(cmd, capture_output=True, text=True).returncode == 0


def ensure_dataset(profile: dict, *, dry_run: bool) -> None:
    print(f"[provision] dataset {profile['bq_dataset']}: create + apply migrations")
    if dry_run:
        return
    env = {**os.environ, "BHAGA_DATASTORE": "bigquery", "BHAGA_BQ_DATASET": profile["bq_dataset"]}
    subprocess.run(
        [sys.executable, "-c",
         "from core.datastore import ensure_dataset, ensure_schema; "
         "ensure_dataset(); print('applied', ensure_schema())"],
        check=True, env=env, cwd=str(PROFILES.parents[3]),
    )


def ensure_bucket(profile: dict, *, dry_run: bool) -> None:
    url = f"gs://{profile['gcs_cache_bucket']}"
    if _exists(["gcloud", "storage", "buckets", "describe", url, f"--project={PROJECT}"]):
        print(f"[provision] bucket {url} exists")
    else:
        _sh(["gcloud", "storage", "buckets", "create", url, f"--project={PROJECT}",
             f"--location={REGION}", "--uniform-bucket-level-access"], dry_run=dry_run)
    for member, role in BUCKET_ACCESS:
        _sh(["gcloud", "storage", "buckets", "add-iam-policy-binding", url,
             f"--member={member}", f"--role={role}"], dry_run=dry_run)


def ensure_scheduler(profile: dict, *, schedule: str, paused: bool, dry_run: bool) -> None:
    name = f"bhaga-nightly-{profile['store_id']}"
    uri = (f"https://{REGION}-run.googleapis.com/apis/run.googleapis.com/v1/"
           f"namespaces/{PROJECT}/jobs/{JOB}:run")
    body = json.dumps(run_request_body(profile))
    verb = ("update" if _exists(["gcloud", "scheduler", "jobs", "describe", name,
                                 f"--project={PROJECT}", f"--location={REGION}"]) else "create")
    _sh(["gcloud", "scheduler", "jobs", verb, "http", name, f"--project={PROJECT}",
         f"--location={REGION}", f"--schedule={schedule}", "--time-zone=America/Chicago",
         f"--uri={uri}", "--http-method=POST", f"--message-body={body}",
         "--headers=Content-Type=application/json",
         f"--oauth-service-account-email={SCHEDULER_SA}",
         "--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform"],
        dry_run=dry_run)
    _sh(["gcloud", "scheduler", "jobs", "pause" if paused else "resume", name,
         f"--project={PROJECT}", f"--location={REGION}"], dry_run=dry_run)
    print(f"[provision] scheduler {name} {verb}d, {'PAUSED' if paused else 'ENABLED'}")


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--store", required=True)
    cli.add_argument("--schedule", default="30 22 * * *",
                     help="Cron in America/Chicago. Keep clear of Austin's 21:30 run: "
                          "both stores share one ADP login and one OTP doc.")
    cli.add_argument("--paused", action="store_true", help="Create the scheduler job paused.")
    cli.add_argument("--skip-dataset", action="store_true")
    cli.add_argument("--dry-run", action="store_true")
    args = cli.parse_args(argv)

    profile = json.loads((PROFILES / f"{args.store}.json").read_text())
    if profile.get("bq_dataset", "bhaga") == "bhaga":
        print(f"[provision] store {args.store} uses the default targets; nothing to provision")
        return 1
    if not args.skip_dataset:
        ensure_dataset(profile, dry_run=args.dry_run)
    ensure_bucket(profile, dry_run=args.dry_run)
    ensure_scheduler(profile, schedule=args.schedule, paused=args.paused, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
