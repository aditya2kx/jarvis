#!/usr/bin/env python3
"""Add a Secret Manager version via ADC. Never prints the payload.

Requires Application Default Credentials (laptop / Cloud Shell). Cursor Cloud
Agents typically cannot run this — see docs/contributing/gcp-access.md.

    python3 scripts/secret_manager_put.py --secret tesla-fleet-client-id --data-file ./value.txt
    python3 scripts/secret_manager_put.py --secret tesla-fleet-client-id --from-env TESLA_CLIENT_ID
    python3 scripts/secret_manager_put.py --secret tesla-fleet-client-id --data-file ./value.txt --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys


DEFAULT_PROJECT = "jarvis-bhaga-prod"


def _read_payload(data_file: str | None, from_env: str | None) -> bytes:
    if bool(data_file) == bool(from_env):
        raise SystemExit("Provide exactly one of --data-file or --from-env")
    if from_env:
        raw = os.environ.get(from_env)
        if raw is None or raw == "":
            raise SystemExit(f"env {from_env} is missing or empty")
        return raw.encode()
    path = data_file or ""
    with open(path, "rb") as fh:
        payload = fh.read()
    if not payload.strip():
        raise SystemExit(f"{path} is empty")
    if payload.endswith(b"\n") and payload.count(b"\n") == 1:
        payload = payload[:-1]
    return payload


def _ensure_secret(sm, project: str, secret_name: str, dry_run: bool) -> str:
    resource = f"projects/{project}/secrets/{secret_name}"
    try:
        sm.get_secret(name=resource)
        print(f"secret exists: {resource}")
        return resource
    except Exception as exc:
        if "404" not in str(exc) and "NOT_FOUND" not in str(exc):
            raise
    print(f"secret missing — will create: {resource}")
    if dry_run:
        print("[dry-run] would create secret (automatic replication)")
        return resource
    from google.cloud.secretmanager_v1.types import Replication, Secret

    sm.create_secret(
        parent=f"projects/{project}",
        secret_id=secret_name,
        secret=Secret(replication=Replication(automatic=Replication.Automatic())),
    )
    print(f"created: {resource}")
    return resource


def put_secret_version(
    *,
    project: str,
    secret_name: str,
    payload: bytes,
    dry_run: bool,
    sm=None,
) -> None:
    if sm is None:
        from google.cloud import secretmanager

        sm = secretmanager.SecretManagerServiceClient()
    resource = _ensure_secret(sm, project, secret_name, dry_run)
    print(f"adding version to {secret_name} ({len(payload)} bytes, value not printed)")
    if dry_run:
        print("[dry-run] would add secret version")
        return
    sm.add_secret_version(parent=resource, payload={"data": payload})
    print("new version added (value not printed)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--secret", required=True, help="Secret Manager secret id")
    parser.add_argument("--data-file", default=None)
    parser.add_argument("--from-env", default=None, help="Read payload from this env var")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    payload = _read_payload(args.data_file, args.from_env)
    put_secret_version(
        project=args.project,
        secret_name=args.secret,
        payload=payload,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
