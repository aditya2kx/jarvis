#!/usr/bin/env python3
"""Provision the SANDBOX_TRIGGER_TOKEN secret on bhaga-webhook.

Idempotent, ADC-based (no gcloud CLI required). Run once after first deploy
of the X-Sandbox-Trigger bypass; re-run with --rotate to issue a new token.

Usage:
    # Dry-run — prints what would happen, no mutations:
    python3 scripts/provision_sandbox_token.py --dry-run

    # Provision (idempotent — skips if secret version already exists):
    python3 scripts/provision_sandbox_token.py

    # Force a new token version (rotation):
    python3 scripts/provision_sandbox_token.py --rotate

    # Non-default targets:
    python3 scripts/provision_sandbox_token.py \\
        --project jarvis-bhaga-prod \\
        --region us-central1 \\
        --service bhaga-webhook \\
        --secret-name sandbox-trigger-token \\
        --env-var SANDBOX_TRIGGER_TOKEN

How it works:
    1. Ensures the Secret Manager secret exists (creates it if not).
    2. Adds a new random version (url-safe 32 bytes) unless one already
       exists and --rotate is not set.
    3. Updates the bhaga-webhook Cloud Run service to mount the secret's
       "latest" version as the SANDBOX_TRIGGER_TOKEN env var. All other
       env vars and secret mounts on the service are preserved.
    4. Waits for the new revision to become ACTIVE.

Token rotation guidance:
    Run with --rotate to generate and store a new token version. The new
    version is immediately pinned via "latest" on the service, so the
    webhook picks it up on the next request without a redeploy. Revoke
    the old version in Secret Manager if needed.

Mount survives image-only deploys:
    deploy.yml uses `gcloud run services update --image ...` which preserves
    existing secret mounts. Re-run this script only when recreating the
    service from scratch.
"""
from __future__ import annotations

import argparse
import secrets
import sys


def _secretmanager_client():
    from google.cloud import secretmanager
    return secretmanager.SecretManagerServiceClient()


def _run_services_client():
    from google.cloud import run_v2
    return run_v2.ServicesClient()


def _ensure_secret(sm, project: str, secret_name: str, dry_run: bool) -> tuple[str, bool]:
    """Ensure the secret exists. Returns (secret_resource_name, already_existed)."""
    resource = f"projects/{project}/secrets/{secret_name}"
    try:
        sm.get_secret(name=resource)
        print(f"  Secret exists: {resource}")
        return resource, True
    except Exception as exc:
        if "404" not in str(exc) and "NOT_FOUND" not in str(exc):
            raise
    print(f"  Secret not found — will create: {resource}")
    if dry_run:
        print("  [dry-run] Would create secret (automatic replication).")
        return resource, False
    from google.cloud.secretmanager_v1.types import Secret, Replication
    sm.create_secret(
        parent=f"projects/{project}",
        secret_id=secret_name,
        secret=Secret(replication=Replication(automatic=Replication.Automatic())),
    )
    print(f"  Created: {resource}")
    return resource, False


def _ensure_version(sm, secret_resource: str, rotate: bool, dry_run: bool) -> bool:
    """Add a new secret version if none exists or --rotate requested.

    Returns True if a new version was added.
    """
    try:
        versions = list(sm.list_secret_versions(parent=secret_resource))
        active = [v for v in versions if v.state.name == "ENABLED"]
    except Exception:
        active = []

    if active and not rotate:
        print(f"  {len(active)} active version(s) found — skipping (use --rotate to add a new one).")
        return False

    action = "Rotating" if active else "Adding first"
    print(f"  {action} secret version ...")
    if dry_run:
        print("  [dry-run] Would add a new random token version.")
        return False
    token_bytes = secrets.token_urlsafe(32).encode()
    sm.add_secret_version(
        parent=secret_resource,
        payload={"data": token_bytes},
    )
    print("  New version added (value not printed).")
    return True


def _mount_secret_on_service(
    svc_client,
    project: str,
    region: str,
    service: str,
    secret_name: str,
    env_var: str,
    dry_run: bool,
) -> None:
    """Mount the secret as an env var on the Cloud Run service.

    Preserves all existing env vars and secret mounts; only replaces/adds
    the entry for env_var.
    """
    from google.cloud import run_v2

    svc_name = f"projects/{project}/locations/{region}/services/{service}"
    print(f"  Fetching service: {svc_name}")
    svc = svc_client.get_service(name=svc_name)

    container = svc.template.containers[0]

    # Build the new env list, replacing any existing entry for this var.
    new_env = [e for e in container.env if e.name != env_var]
    new_entry = run_v2.EnvVar(
        name=env_var,
        value_source=run_v2.EnvVarSource(
            secret_key_ref=run_v2.SecretKeySelector(
                secret=secret_name,
                version="latest",
            )
        ),
    )
    new_env.append(new_entry)

    print(f"  Mounting {env_var} -> secretKeyRef/{secret_name}:latest")
    if dry_run:
        print("  [dry-run] Would update service with new env mount.")
        return

    container.env[:] = new_env
    op = svc_client.update_service(service=svc)
    print("  Waiting for new revision to become active ...")
    updated_svc = op.result(timeout=300)
    print(f"  Active revision: {updated_svc.latest_ready_revision}")


def _verify_mount(svc_client, project: str, region: str, service: str, env_var: str) -> None:
    """Print the current mount for env_var on the service (no token value shown)."""
    svc_name = f"projects/{project}/locations/{region}/services/{service}"
    svc = svc_client.get_service(name=svc_name)
    container = svc.template.containers[0]
    for e in container.env:
        if e.name == env_var:
            if e.value_source and e.value_source.secret_key_ref:
                ref = e.value_source.secret_key_ref
                print(f"  Verified mount: {env_var} -> {ref.secret}:{ref.version}")
            else:
                print(f"  Found {env_var} as plain value (expected secret mount — re-run)")
            return
    print(f"  WARNING: {env_var} not found in service env after update.")


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cli.add_argument("--project", default="jarvis-bhaga-prod")
    cli.add_argument("--region", default="us-central1")
    cli.add_argument("--service", default="bhaga-webhook")
    cli.add_argument("--secret-name", default="sandbox-trigger-token")
    cli.add_argument("--env-var", default="SANDBOX_TRIGGER_TOKEN")
    cli.add_argument("--rotate", action="store_true",
                     help="Force a new token version even if one already exists.")
    cli.add_argument("--dry-run", action="store_true",
                     help="Print what would happen without making any mutations.")
    args = cli.parse_args(argv)

    if args.dry_run:
        print("[provision_sandbox_token] DRY RUN — no mutations.")

    print("\n--- Step 1: Secret Manager ---")
    sm = _secretmanager_client()
    secret_resource, _ = _ensure_secret(sm, args.project, args.secret_name, args.dry_run)
    _ensure_version(sm, secret_resource, args.rotate, args.dry_run)

    print("\n--- Step 2: Cloud Run service mount ---")
    svc_client = _run_services_client()
    _mount_secret_on_service(
        svc_client,
        args.project,
        args.region,
        args.service,
        args.secret_name,
        args.env_var,
        args.dry_run,
    )

    if not args.dry_run:
        print("\n--- Step 3: Verify ---")
        _verify_mount(svc_client, args.project, args.region, args.service, args.env_var)

    print("\n[provision_sandbox_token] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
