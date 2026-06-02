#!/usr/bin/env python3
"""Live sandbox run — deploy UNMERGED PR code to a Cloud Run sandbox job and
scrape Square/ADP for real, writing ONLY to sandbox targets.

WHY THIS EXISTS
  The replay sandbox e2e (``sandbox_e2e.py``) proves the pure model/allocation
  core by replaying the GCS cache — zero browser, zero OTP. It CANNOT exercise a
  live scrape, so it can't reproduce or prove a fix for selector drift like the
  2026-05-31 item-sales 'date picker not found' incident. This runner closes that
  gap: it deploys the current branch's image to a dedicated Cloud Run job
  (``bhaga-sandbox-refresh``) and runs the real pipeline against the live portals,
  but every write is diverted to a sandbox target.

SANDBOX ISOLATION (hard invariant — see .cursor/rules/bhaga-principles.md)
  A sandbox run may READ prod data (the GCS scrape cache, raw sheets) but must
  NEVER write to a prod data source. This runner enforces that three ways and
  fails loud before it ever executes the job:
    * Sheets   → BHAGA_SHEET_MODE=staging + BHAGA_STAGING_*_SID (leased pool slot);
                 core.config_loader._assert_not_production_sheet blocks prod sheets.
    * GCS cache→ BHAGA_GCS_CACHE_WRITE_BUCKET=<sandbox bucket> (reads still come
                 from prod bhaga-scrape-cache); gcs_cache._assert_sandbox_write_isolation
                 blocks a prod-bucket write.
    * Run state→ BHAGA_FIRESTORE_COLLECTION=<sandbox collection>;
                 state_adapter._assert_sandbox_state_isolation blocks prod 'runs'.

OTP ROUTING (operator design, 2026-06-01)
  The run uses the SAME prod BHAGA cloud Slack bot, but its OTP prompt is labeled
  with the run env + PR (BHAGA_RUN_ENV / BHAGA_RUN_LABEL) and its pending-OTP
  checkpoint carries routing metadata (env / label / target_job / collection), so
  the operator's reply resumes the SANDBOX job — not prod — even if a prod run is
  awaiting OTP at the same time (sandbox takes precedence in the webhook).

Usage (CI, via .github/workflows/sandbox-live-run.yml workflow_dispatch):
    python3 -m agents.bhaga.scripts.sandbox_live_run \\
        --store palmetto --pr-number 42 --pr-label "fix/item-sales-selectors" \\
        --refresh-date 2026-05-31 --image <registry>/bhaga-orchestrator:<sha>

    # Deploy + execute, or stop after deploy with --no-execute (dry deploy).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import sandbox_provision  # noqa: E402

PROJECT_ID = os.environ.get("BHAGA_PROJECT_ID", "jarvis-bhaga-prod")
REGION = os.environ.get("BHAGA_REGION", "us-central1")
PROD_JOB_NAME = "bhaga-daily-refresh"
SANDBOX_JOB_NAME = os.environ.get("BHAGA_SANDBOX_JOB_NAME", "bhaga-sandbox-refresh")

# Sandbox write targets (NEVER the prod equivalents). Reads still come from prod
# (BHAGA_GCS_CACHE_BUCKET defaults to the prod cache), honoring "read prod, write
# sandbox".
SANDBOX_CACHE_WRITE_BUCKET = os.environ.get(
    "BHAGA_SANDBOX_CACHE_BUCKET", "bhaga-scrape-cache-sandbox"
)
SANDBOX_RUNS_COLLECTION = os.environ.get("BHAGA_SANDBOX_RUNS_COLLECTION", "sandbox_runs")

# The canonical PROD values a sandbox run must never write.
_PROD_CACHE_BUCKET = "bhaga-scrape-cache"
_PROD_RUNS_COLLECTION = "runs"


# ── Pure helpers (no I/O — unit-tested) ───────────────────────────


def sandbox_job_resource(job_name: str = SANDBOX_JOB_NAME) -> str:
    """Fully-qualified Cloud Run job resource name (for OTP routing metadata)."""
    return f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{job_name}"


def build_sandbox_env(
    *,
    staging_ids: dict[str, str],
    refresh_date: str,
    store: str,
    run_label: str,
    cache_write_bucket: str = SANDBOX_CACHE_WRITE_BUCKET,
    runs_collection: str = SANDBOX_RUNS_COLLECTION,
    target_job: str | None = None,
) -> dict[str, str]:
    """Construct the sandbox job's env overlay.

    Layers the isolation overrides (staging sheets, sandbox cache write bucket,
    sandbox run-state collection) plus the OTP-routing labels on top of the
    prod-like base. Pure: returns a dict, performs no I/O.
    """
    env: dict[str, str] = {
        # Isolation: sheets.
        "BHAGA_SHEET_MODE": "staging",
        # Isolation: GCS cache — write to sandbox, read may still hit prod.
        "BHAGA_GCS_CACHE_WRITE_BUCKET": cache_write_bucket,
        # Isolation: Firestore run-state.
        "BHAGA_FIRESTORE_COLLECTION": runs_collection,
        # OTP routing / observability labels.
        "BHAGA_RUN_ENV": "sandbox",
        "BHAGA_RUN_LABEL": run_label,
        "BHAGA_OTP_TARGET_JOB": target_job or sandbox_job_resource(),
        # The business date being reproduced.
        "REFRESH_DATE": refresh_date,
        "STORE": store,
    }
    # Route the pipeline to the leased sandbox sheets.
    env.update(sandbox_provision.staging_env(staging_ids))
    return env


def assert_sandbox_isolation(env: dict[str, str]) -> None:
    """Fail loud BEFORE deploy if the env would let a sandbox run touch prod.

    Mirrors the runtime guards (sheet / GCS / Firestore) so a misconfiguration is
    caught at the orchestration layer too, never after a prod write.
    """
    if env.get("BHAGA_SHEET_MODE", "").lower() != "staging":
        raise RuntimeError("sandbox isolation: BHAGA_SHEET_MODE must be 'staging'")
    write_bucket = env.get("BHAGA_GCS_CACHE_WRITE_BUCKET", "")
    if not write_bucket or write_bucket == _PROD_CACHE_BUCKET:
        raise RuntimeError(
            f"sandbox isolation: BHAGA_GCS_CACHE_WRITE_BUCKET must be a sandbox bucket, "
            f"got {write_bucket!r} (prod cache is {_PROD_CACHE_BUCKET!r})"
        )
    collection = env.get("BHAGA_FIRESTORE_COLLECTION", "")
    if not collection or collection == _PROD_RUNS_COLLECTION:
        raise RuntimeError(
            f"sandbox isolation: BHAGA_FIRESTORE_COLLECTION must be a sandbox collection, "
            f"got {collection!r} (prod is {_PROD_RUNS_COLLECTION!r})"
        )
    # Every staging sheet SID must be present so resolve_sheet_id never falls back
    # to a prod sheet (the staging guard would block it anyway, but be explicit).
    missing = [
        sandbox_provision.staging_env_key(k)
        for k in sandbox_provision.PROFILE_KEYS
        if not env.get(sandbox_provision.staging_env_key(k))
    ]
    if missing:
        raise RuntimeError(f"sandbox isolation: missing staging sheet IDs: {missing}")


def env_flag_args(env: dict[str, str]) -> list[str]:
    """Render an env dict as a single ``--set-env-vars`` gcloud flag value.

    Uses the ``^@^`` custom delimiter so values containing commas are safe.
    """
    pairs = "@".join(f"{k}={v}" for k, v in sorted(env.items()))
    return ["--set-env-vars", f"^@^{pairs}"]


# ── gcloud I/O ────────────────────────────────────────────────────


def _gcloud(args: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = ["gcloud", *args, f"--project={PROJECT_ID}"]
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True,
                          capture_output=capture)


def job_exists(job_name: str) -> bool:
    r = _gcloud(["run", "jobs", "describe", job_name, f"--region={REGION}",
                 "--format=value(name)"], check=False, capture=True)
    return r.returncode == 0


def ensure_sandbox_bucket(bucket: str) -> None:
    """Create the sandbox cache bucket if it does not exist (idempotent)."""
    r = _gcloud(["storage", "buckets", "describe", f"gs://{bucket}",
                 "--format=value(name)"], check=False, capture=True)
    if r.returncode == 0:
        print(f"  sandbox bucket exists: gs://{bucket}")
        return
    print(f"  creating sandbox bucket gs://{bucket}")
    _gcloud(["storage", "buckets", "create", f"gs://{bucket}",
             f"--location={REGION}", "--uniform-bucket-level-access"])


def deploy_sandbox_job(*, image: str, env: dict[str, str], base_job: str = PROD_JOB_NAME) -> None:
    """Create-or-update the sandbox job from the PR image + sandbox env overlay.

    The job mirrors the prod orchestrator (same secrets/SA/resources). We do NOT
    point a scheduler at it — it is execute-on-demand only.
    """
    flags = [f"--region={REGION}", f"--image={image}", *env_flag_args(env)]
    if job_exists(SANDBOX_JOB_NAME):
        print(f"  updating existing sandbox job {SANDBOX_JOB_NAME}")
        _gcloud(["run", "jobs", "update", SANDBOX_JOB_NAME, *flags])
    else:
        print(f"  creating sandbox job {SANDBOX_JOB_NAME} (mirrors {base_job} secrets/SA)")
        # Inherit the prod job's secrets + service account by cloning its config
        # is not a single gcloud call; the operator wires secrets once (see
        # RUNBOOK §13.1). Here we create with the image + env; secrets/SA are
        # attached by the one-time setup documented in the RUNBOOK.
        _gcloud(["run", "jobs", "create", SANDBOX_JOB_NAME, *flags])


def execute_job(*, wait: bool = True) -> int:
    args = ["run", "jobs", "execute", SANDBOX_JOB_NAME, f"--region={REGION}"]
    if wait:
        args.append("--wait")
    r = _gcloud(args, check=False)
    return r.returncode


# ── Orchestration ─────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--pr-number", type=int, required=True)
    cli.add_argument("--pr-label", required=True,
                     help="Human PR label shown in the sandbox OTP prompt (e.g. branch/PR title).")
    cli.add_argument("--refresh-date", required=True, help="Business date to reproduce (YYYY-MM-DD).")
    cli.add_argument("--image", required=True,
                     help="Fully-qualified orchestrator image built from the PR branch.")
    cli.add_argument("--no-execute", action="store_true",
                     help="Provision + deploy the sandbox job but do not run it (dry deploy).")
    cli.add_argument("--keep", action="store_true",
                     help="Do not release the sandbox slot afterwards (debugging).")
    args = cli.parse_args(argv)

    run_label = f"PR#{args.pr_number} {args.pr_label}"

    print(f"[sandbox_live_run] provisioning slot for {run_label} ...")
    prov = sandbox_provision.provision(store=args.store, pr_number=args.pr_number)
    ids = prov["ids"]

    env = build_sandbox_env(
        staging_ids=ids,
        refresh_date=args.refresh_date,
        store=args.store,
        run_label=run_label,
    )
    assert_sandbox_isolation(env)  # fail loud BEFORE any deploy/execute
    print("[sandbox_live_run] isolation pre-flight OK (sheets/cache/firestore all sandbox)")

    try:
        ensure_sandbox_bucket(SANDBOX_CACHE_WRITE_BUCKET)
        deploy_sandbox_job(image=args.image, env=env)
        print(f"[sandbox_live_run] deployed {SANDBOX_JOB_NAME} @ {args.image}")

        if args.no_execute:
            print("[sandbox_live_run] --no-execute: stopping after deploy.")
            return 0

        print(f"[sandbox_live_run] executing {SANDBOX_JOB_NAME} for {args.refresh_date} "
              f"(OTP prompt will be labeled '{run_label}') ...")
        rc = execute_job(wait=True)
        print(f"[sandbox_live_run] job execution finished rc={rc}")
        return rc
    finally:
        if not args.keep:
            try:
                sandbox_provision.teardown(store=args.store, pr_number=args.pr_number)
            except Exception as exc:  # noqa: BLE001
                print(f"[sandbox_live_run] WARN: slot teardown failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
