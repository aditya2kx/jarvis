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
import json
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
        # Operator-supervised: wait for the OTP code INLINE rather than the
        # checkpoint-and-resume handshake, so the existing prod webhook delivers
        # the code (works even before this PR's webhook routing is deployed).
        "BHAGA_OTP_ASSUME_READY": "1",
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


def _find_containers(job_json: dict) -> list[dict]:
    """First container list in the describe JSON, regardless of schema nesting.

    gcloud emits either the v2 shape (template.template.containers) or the KRM/v1
    shape (spec.template.spec.template.spec.containers); a recursive search for the
    first ``containers`` list is robust to both.
    """
    found: list[list] = []

    def walk(node: object) -> None:
        if found:
            return
        if isinstance(node, dict):
            for key, val in node.items():
                if key == "containers" and isinstance(val, list) and val:
                    found.append(val)
                    return
                walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(job_json)
    return found[0] if found else []


def _find_scalar(job_json: dict, key: str):
    """First scalar value for ``key`` anywhere in the JSON (schema-agnostic)."""
    out = [None]

    def walk(node: object) -> None:
        if out[0] is not None:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if k == key and not isinstance(v, (dict, list)) and v not in (None, ""):
                    out[0] = v
                    return
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(job_json)
    return out[0]


def parse_secret_flags(job_json: dict) -> list[str]:
    """Build a ``--set-secrets`` flag mirroring the prod job's secret bindings.

    The sandbox scrapes the REAL portals, so it needs the SAME credentials as
    prod — only the isolation env differs. Inheriting prod's bindings means no
    separate sandbox secret store and no manual secret wiring. Handles both the
    v2 (``valueSource.secretKeyRef`` → secret/version) and KRM
    (``valueFrom.secretKeyRef`` → name/key) shapes. Returns [] when none.
    """
    mounts: list[str] = []
    for container in _find_containers(job_json):
        for entry in container.get("env", []) or []:
            ref = (entry.get("valueSource") or entry.get("valueFrom") or {}).get("secretKeyRef")
            if not ref:
                continue
            env_name = entry.get("name")
            secret = ref.get("secret") or ref.get("name")
            version = ref.get("version") or ref.get("key") or "latest"
            if env_name and secret:
                mounts.append(f"{env_name}={secret}:{version}")
    return ["--set-secrets", ",".join(mounts)] if mounts else []


def parse_service_account(job_json: dict) -> str | None:
    """Extract the prod job's service account (schema-agnostic)."""
    return (_find_scalar(job_json, "serviceAccountName")
            or _find_scalar(job_json, "serviceAccount"))


def parse_resource_flags(job_json: dict) -> list[str]:
    """Mirror prod's cpu/memory/timeout/retries so the sandbox job can run a browser.

    A freshly-created Cloud Run job defaults to 512Mi / 600s, which would OOM or
    time out a Chromium scrape (and the inline-OTP wait). Inheriting prod's limits
    keeps the sandbox run faithful.
    """
    flags: list[str] = []
    containers = _find_containers(job_json)
    limits = ((containers[0] if containers else {}).get("resources") or {}).get("limits") or {}
    if limits.get("cpu"):
        flags += ["--cpu", str(limits["cpu"])]
    if limits.get("memory"):
        flags += ["--memory", str(limits["memory"])]
    timeout = _find_scalar(job_json, "timeoutSeconds") or _find_scalar(job_json, "timeout")
    if timeout is not None:
        ts = str(timeout)
        flags += ["--task-timeout", ts if ts.endswith("s") else f"{ts}s"]
    retries = _find_scalar(job_json, "maxRetries")
    if retries is not None:
        flags += ["--max-retries", str(retries)]
    return flags


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


def assert_sandbox_bucket(bucket: str) -> None:
    """Verify the sandbox cache bucket exists; fail with remediation if not.

    By least privilege the run service account has GCS *read* (+ object write on
    the sandbox bucket) but NOT project-level bucket-create, so the bucket is a
    one-time operator setup (like the sandbox sheet pool), not a CI action.
    """
    r = _gcloud(["storage", "buckets", "describe", f"gs://{bucket}",
                 "--format=value(name)"], check=False, capture=True)
    if r.returncode == 0:
        print(f"  sandbox bucket exists: gs://{bucket}")
        return
    raise SystemExit(
        f"\nsandbox bucket gs://{bucket} is missing. Create it once (operator):\n"
        f"  gcloud storage buckets create gs://{bucket} \\\n"
        f"    --location={REGION} --uniform-bucket-level-access --project={PROJECT_ID}\n"
        f"  # grant the run SA object read/write on it (objectAdmin), e.g.:\n"
        f"  gcloud storage buckets add-iam-policy-binding gs://{bucket} \\\n"
        f"    --member=serviceAccount:<run-sa> --role=roles/storage.objectAdmin\n"
        f"See RUNBOOK.md §13 (live sandbox run setup)."
    )


def _describe_prod_job() -> dict:
    """Fetch the prod job config (for secret + SA inheritance). {} on failure."""
    r = _gcloud(["run", "jobs", "describe", PROD_JOB_NAME, f"--region={REGION}", "--format=json"],
                check=False, capture=True)
    if r.returncode != 0 or not (r.stdout or "").strip():
        print(f"  WARN: could not read {PROD_JOB_NAME} config — sandbox secrets/SA must be wired manually")
        return {}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}


def deploy_sandbox_job(*, image: str, env: dict[str, str], base_job: str = PROD_JOB_NAME) -> None:
    """Create-or-update the sandbox job from the PR image + sandbox env overlay.

    On CREATE the job self-wires by inheriting the prod orchestrator's secret
    bindings + service account (same creds — only the isolation env differs), so
    there is no separate sandbox secret store and no manual wiring. We never point
    a scheduler at it — execute-on-demand only.
    """
    flags = [f"--region={REGION}", f"--image={image}", *env_flag_args(env)]
    if job_exists(SANDBOX_JOB_NAME):
        print(f"  updating existing sandbox job {SANDBOX_JOB_NAME}")
        _gcloud(["run", "jobs", "update", SANDBOX_JOB_NAME, *flags])
        return

    prod = _describe_prod_job()
    secret_flags = parse_secret_flags(prod)
    if secret_flags:
        n = secret_flags[1].count(",") + 1
        print(f"  inheriting {n} secret binding(s) from {base_job}")
    sa = parse_service_account(prod)
    sa_flags = [f"--service-account={sa}"] if sa else []
    if sa:
        print(f"  inheriting service account {sa}")
    resource_flags = parse_resource_flags(prod)
    if resource_flags:
        print(f"  inheriting resources/timeout: {' '.join(resource_flags)}")
    print(f"  creating sandbox job {SANDBOX_JOB_NAME} (mirrors {base_job} secrets/SA/resources)")
    _gcloud(["run", "jobs", "create", SANDBOX_JOB_NAME, *flags,
             *secret_flags, *sa_flags, *resource_flags])


def execute_job(*, wait: bool = True) -> int:
    args = ["run", "jobs", "execute", SANDBOX_JOB_NAME, f"--region={REGION}"]
    if wait:
        args.append("--wait")
    r = _gcloud(args, check=False)
    return r.returncode


def _write_evidence(path: str, *, run_label: str, refresh_date: str, rc: int) -> None:
    """Emit a markdown summary the workflow posts back as a PR comment."""
    status = "✅ passed" if rc == 0 else f"❌ failed (rc={rc})"
    evidence = f"gs://{SANDBOX_CACHE_WRITE_BUCKET}/{refresh_date}/evidence/"
    body = (
        f"### BHAGA live sandbox run — {status}\n\n"
        f"- **scenario / label:** `{run_label}`\n"
        f"- **date:** `{refresh_date}`\n"
        f"- **job:** `{SANDBOX_JOB_NAME}` (image deployed from this branch)\n"
        f"- **isolation:** staging sheets · write bucket `{SANDBOX_CACHE_WRITE_BUCKET}` · "
        f"collection `{SANDBOX_RUNS_COLLECTION}` (reads prod, writes sandbox)\n"
        f"- **failure evidence (if any):** `{evidence}` (screenshot + DOM + meta)\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    print(f"[sandbox_live_run] wrote evidence summary → {path}")


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
    cli.add_argument("--evidence-file",
                     help="Write a markdown evidence summary here (for the PR comment).")
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
        assert_sandbox_bucket(SANDBOX_CACHE_WRITE_BUCKET)
        deploy_sandbox_job(image=args.image, env=env)
        print(f"[sandbox_live_run] deployed {SANDBOX_JOB_NAME} @ {args.image}")

        if args.no_execute:
            print("[sandbox_live_run] --no-execute: stopping after deploy.")
            return 0

        print(f"[sandbox_live_run] executing {SANDBOX_JOB_NAME} for {args.refresh_date} "
              f"(OTP prompt will be labeled '{run_label}') ...")
        rc = execute_job(wait=True)
        print(f"[sandbox_live_run] job execution finished rc={rc}")
        if args.evidence_file:
            _write_evidence(args.evidence_file, run_label=run_label,
                            refresh_date=args.refresh_date, rc=rc)
        return rc
    finally:
        if not args.keep:
            try:
                sandbox_provision.teardown(store=args.store, pr_number=args.pr_number)
            except Exception as exc:  # noqa: BLE001
                print(f"[sandbox_live_run] WARN: slot teardown failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
