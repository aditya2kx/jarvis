#!/usr/bin/env python3
"""Centralized credential registry for Jarvis.

Tracks every portal, service, and API credential — what type it is,
where it's stored, and which account it belongs to. Secrets stay in
macOS Keychain or OAuth token files; this registry only stores metadata.

Registry data lives in registry.json (gitignored) alongside this module.

Backend selection (env var BHAGA_SECRETS_BACKEND):
  - "keychain" (default): macOS Keychain via `security` CLI
  - "gcp": GCP Secret Manager (project: jarvis-bhaga-prod)
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

try:
    from google.cloud import secretmanager as _secretmanager
except ImportError:
    _secretmanager = None

__all__ = [
    "lookup", "verify", "audit_all", "add_keychain",
    "list_all", "register", "remove",
    "get_secret", "mirror_to_gcp",
    "hydrate", "hydrate_all",
]

_GCP_PROJECT = "jarvis-bhaga-prod"

BHAGA_SECRETS = frozenset([
    "adp_palmetto_login",
    "square_palmetto_oauth",
    "slack_bhaga_app",
    "slack_bhaga_bot",
    "slack_bhaga_cloud_bot",
    "slack_bhaga_cloud_signing",
    "google_palmetto",
    "clickup",
    "clickup_palmetto_pat",
])

# Committed map: GCP Secret Manager secret name -> Keychain metadata.
# Works on a fresh clone without the gitignored registry.json.
# Used by hydrate() to bootstrap any provider without re-discovery.
# Providers: ClickUp, Google, Square, ADP, Slack.
SECRET_TO_KEYCHAIN: dict[str, dict[str, str]] = {
    "jarvis-clickup-palmetto-pat": {
        "service": "jarvis-clickup-palmetto-pat",
        "account": "CLICKUP_PAT",
    },
    "google_palmetto": {
        "service": "google_palmetto",
        "account": "palmetto_google_oauth",
    },
    "square_palmetto_oauth": {
        "service": "square_palmetto_oauth",
        "account": "palmetto_square",
    },
    "adp_palmetto_login": {
        "service": "adp_palmetto_login",
        "account": "palmetto_adp",
    },
    "slack_bhaga_bot": {
        "service": "slack_bhaga_bot",
        "account": "bhaga_slack_bot",
    },
    "slack_bhaga_cloud_bot": {
        "service": "slack_bhaga_cloud_bot",
        "account": "bhaga_cloud_slack_bot",
    },
}


def _secrets_backend() -> str:
    return os.environ.get("BHAGA_SECRETS_BACKEND", "keychain").lower()

_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry.json")


def _load_registry():
    if not os.path.exists(_REGISTRY_PATH):
        return {}
    with open(_REGISTRY_PATH) as f:
        return json.load(f)


def _save_registry(data):
    with open(_REGISTRY_PATH, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def lookup(name):
    """Look up a credential by name. Returns the metadata dict or None."""
    return _load_registry().get(name)


def list_all():
    """Return all registered credentials (metadata only, no secrets)."""
    return _load_registry()


def get_secret(name: str) -> str:
    """Retrieve the actual secret value for a registry entry.

    Backend selection via BHAGA_SECRETS_BACKEND env var:
      - "keychain" (default): reads from macOS Keychain
      - "gcp": reads from GCP Secret Manager
    """
    backend = _secrets_backend()

    if backend == "gcp":
        if _secretmanager is None:
            raise ImportError(
                "google-cloud-secret-manager is not installed. "
                "Install it with: pip install google-cloud-secret-manager"
            )
        client = _secretmanager.SecretManagerServiceClient()
        secret_path = f"projects/{_GCP_PROJECT}/secrets/{name}/versions/latest"
        response = client.access_secret_version(name=secret_path)
        return response.payload.data.decode("utf-8")

    reg = _load_registry()
    entry = reg.get(name)
    if entry is None:
        raise KeyError(f"Credential '{name}' not found in registry")

    # Default: keychain backend
    cred_type = entry.get("type", "")

    if cred_type == "keychain":
        service = entry.get("service", "")
        account = entry.get("account", "")
        if not service:
            raise ValueError(f"No keychain service specified for '{name}'")
        cmd = ["security", "find-generic-password", "-s", service]
        if account:
            cmd += ["-a", account]
        cmd.append("-w")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            raise RuntimeError(
                f"Keychain lookup failed for '{name}' "
                f"(service={service}): {result.stderr.strip()}"
            )
        return result.stdout.strip()

    if cred_type == "oauth_file":
        path = os.path.expanduser(entry.get("path", ""))
        if not path:
            raise ValueError(f"No path specified for oauth_file '{name}'")
        if os.path.isdir(path):
            creds_file = os.path.join(path, ".gdrive-server-credentials.json")
            if not os.path.exists(creds_file):
                raise FileNotFoundError(f"OAuth file missing at {creds_file}")
            with open(creds_file) as f:
                return f.read()
        if not os.path.exists(path):
            raise FileNotFoundError(f"OAuth file missing at {path}")
        with open(path) as f:
            return f.read()

    raise ValueError(f"Unsupported credential type '{cred_type}' for '{name}'")


def hydrate(name: str, *, force: bool = False) -> str:
    """Pull a secret from GCP Secret Manager (via ADC) and store it in Keychain.

    Works for any provider in SECRET_TO_KEYCHAIN — ClickUp, Google, Square,
    ADP, Slack — without needing the gcloud CLI binary.

    Args:
        name: GCP Secret Manager secret name, e.g. 'jarvis-clickup-palmetto-pat'.
        force: Re-hydrate even if the Keychain entry already exists.

    Returns:
        The Keychain service name the secret was stored under.

    The secret value is NEVER printed. If ADC credentials are absent, a clear
    actionable message is printed: run 'gcloud auth application-default login'.
    """
    if name not in SECRET_TO_KEYCHAIN:
        raise KeyError(
            f"Secret {name!r} not in SECRET_TO_KEYCHAIN committed map. "
            f"Known: {sorted(SECRET_TO_KEYCHAIN)}"
        )
    kc = SECRET_TO_KEYCHAIN[name]
    service = kc["service"]
    account = kc["account"]

    # Skip if already present and not forced.
    if not force:
        probe = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if probe.returncode == 0:
            print(f"[hydrate] {name}: already in Keychain (service={service}), skipping.")
            return service

    # Read from GCP Secret Manager via ADC (no gcloud binary required).
    try:
        import base64 as _b64
        import urllib.request as _req
        try:
            import google.auth as _gauth
            from google.auth.transport.requests import Request as _Req
            creds, _ = _gauth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            creds.refresh(_Req())
        except Exception as e:
            raise RuntimeError(
                f"ADC credentials unavailable: {e}. "
                f"Run: gcloud auth application-default login"
            ) from e

        url = (
            f"https://secretmanager.googleapis.com/v1"
            f"/projects/{_GCP_PROJECT}/secrets/{name}/versions/latest:access"
        )
        request = _req.Request(
            url, headers={"Authorization": f"Bearer {creds.token}"}
        )
        with _req.urlopen(request, timeout=30) as resp:
            payload = json.loads(resp.read())
        secret_value = _b64.b64decode(payload["payload"]["data"]).decode().strip()
    except Exception as e:
        raise RuntimeError(
            f"Failed to fetch secret {name!r} from Secret Manager: {e}"
        ) from e

    # Write to Keychain — value never printed.
    subprocess.run(
        ["security", "delete-generic-password", "-s", service, "-a", account],
        capture_output=True,
    )
    result = subprocess.run(
        ["security", "add-generic-password", "-s", service, "-a", account,
         "-w", secret_value, "-U"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to write secret to Keychain (service={service}): "
            f"{result.stderr.strip()}"
        )
    print(f"[hydrate] {name}: stored in Keychain (service={service})")
    return service


def hydrate_all(*, force: bool = False) -> dict[str, str]:
    """Hydrate every secret in SECRET_TO_KEYCHAIN that is missing locally.

    Returns a dict mapping secret name -> 'hydrated' | 'already_present' | error.
    """
    results: dict[str, str] = {}
    for name in sorted(SECRET_TO_KEYCHAIN):
        try:
            service = hydrate(name, force=force)
            results[name] = "hydrated" if force else "ok"
        except Exception as exc:
            results[name] = f"error: {exc}"
            print(f"[hydrate_all] {name}: FAILED — {exc}", file=sys.stderr)
    return results


def mirror_to_gcp() -> dict[str, str]:
    """Mirror BHAGA-relevant secrets from Keychain/local files to GCP Secret Manager.

    Iterates BHAGA_SECRETS, reads each from the local backend (keychain/file),
    and creates or updates the corresponding GCP Secret Manager secret.

    Returns a dict mapping secret name -> status ("mirrored" | "skipped" | error message).
    """
    if _secretmanager is None:
        raise ImportError(
            "google-cloud-secret-manager is not installed. "
            "Install it with: pip install google-cloud-secret-manager"
        )

    client = _secretmanager.SecretManagerServiceClient()
    parent = f"projects/{_GCP_PROJECT}"
    reg = _load_registry()
    results: dict[str, str] = {}

    for name in sorted(reg.keys()):
        if name not in BHAGA_SECRETS:
            results[name] = "skipped (not BHAGA-relevant)"
            continue

        entry = reg[name]
        try:
            # Read the secret value from local backend
            cred_type = entry.get("type", "")
            if cred_type == "keychain":
                service = entry.get("service", "")
                account = entry.get("account", "")
                cmd = ["security", "find-generic-password", "-s", service]
                if account:
                    cmd += ["-a", account]
                cmd.append("-w")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if result.returncode != 0:
                    results[name] = f"error: keychain read failed ({result.stderr.strip()})"
                    continue
                payload = result.stdout.strip()
            elif cred_type == "oauth_file":
                path = os.path.expanduser(entry.get("path", ""))
                if os.path.isdir(path):
                    path = os.path.join(path, ".gdrive-server-credentials.json")
                if not os.path.exists(path):
                    results[name] = f"error: file not found at {path}"
                    continue
                with open(path) as f:
                    payload = f.read()
            else:
                results[name] = f"skipped (unsupported type: {cred_type})"
                continue

            # Create secret if it doesn't exist, then add version
            secret_path = f"{parent}/secrets/{name}"
            try:
                client.get_secret(request={"name": secret_path})
            except Exception:
                client.create_secret(
                    request={
                        "parent": parent,
                        "secret_id": name,
                        "secret": {"replication": {"automatic": {}}},
                    }
                )

            client.add_secret_version(
                request={
                    "parent": secret_path,
                    "payload": {"data": payload.encode("utf-8")},
                }
            )
            results[name] = "mirrored"
            print(f"  [mirror] {name}: mirrored to GCP Secret Manager")

        except Exception as exc:
            results[name] = f"error: {type(exc).__name__}: {exc}"
            print(f"  [mirror] {name}: FAILED — {exc}")

    mirrored = sum(1 for v in results.values() if v == "mirrored")
    skipped = sum(1 for v in results.values() if "skipped" in v)
    errors = sum(1 for v in results.values() if v.startswith("error"))
    print(f"\n  [mirror] Summary: {mirrored} mirrored, {skipped} skipped, {errors} errors")
    return results


def verify(name):
    """Check if a credential is actually present and accessible.

    Returns a dict with 'ok' (bool), 'name', and 'detail' (str).
    Respects BHAGA_SECRETS_BACKEND: on "gcp", verifies via Secret Manager.
    """
    reg = _load_registry()
    entry = reg.get(name)
    if entry is None:
        return {"ok": False, "name": name, "detail": "Not in registry"}

    backend = _secrets_backend()

    if backend == "gcp":
        if _secretmanager is None:
            return {"ok": False, "name": name, "detail": "google-cloud-secret-manager not installed"}
        try:
            client = _secretmanager.SecretManagerServiceClient()
            secret_path = f"projects/{_GCP_PROJECT}/secrets/{name}/versions/latest"
            client.access_secret_version(name=secret_path)
            return {"ok": True, "name": name, "detail": f"GCP Secret Manager: found {name}"}
        except Exception as exc:
            return {"ok": False, "name": name, "detail": f"GCP Secret Manager error: {exc}"}

    # Default: keychain backend
    cred_type = entry.get("type", "")

    if cred_type == "keychain":
        service = entry.get("service", "")
        if not service:
            return {"ok": False, "name": name, "detail": "No keychain service specified"}
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return {"ok": True, "name": name, "detail": f"Keychain entry found for service={service}"}
        return {"ok": False, "name": name, "detail": f"Keychain entry missing for service={service}"}

    if cred_type == "oauth_file":
        path = os.path.expanduser(entry.get("path", ""))
        if not path:
            return {"ok": False, "name": name, "detail": "No path specified"}
        if os.path.isdir(path):
            creds_file = os.path.join(path, ".gdrive-server-credentials.json")
            if os.path.exists(creds_file):
                return {"ok": True, "name": name, "detail": f"OAuth credentials file found at {creds_file}"}
            return {"ok": False, "name": name, "detail": f"OAuth credentials file missing at {creds_file}"}
        if os.path.exists(path):
            return {"ok": True, "name": name, "detail": f"OAuth file found at {path}"}
        return {"ok": False, "name": name, "detail": f"OAuth file missing at {path}"}

    return {"ok": False, "name": name, "detail": f"Unknown credential type: {cred_type}"}


def audit_all():
    """Verify every registered credential. Returns list of verify results.

    When a credential is missing, the detail field includes the exact fix command:
        python3 -m skills.credentials.registry hydrate <name>
    """
    reg = _load_registry()
    results = []
    for name in sorted(reg.keys()):
        r = verify(name)
        if not r["ok"]:
            # Emit a fix hint when a hydrate mapping exists for this credential.
            hint_key = next(
                (k for k, v in SECRET_TO_KEYCHAIN.items()
                 if v["service"] == reg[name].get("service", "")),
                None,
            )
            if hint_key:
                r["detail"] += (
                    f"  Fix: python3 -m skills.credentials.registry hydrate {hint_key}"
                )
        results.append(r)
    return results


def register(name, entry):
    """Add or update a credential entry in the registry.

    Args:
        name: Unique credential key (e.g. 'slack_chanakya', 'schwab')
        entry: Dict with at least 'type' and relevant fields
    """
    reg = _load_registry()
    reg[name] = entry
    _save_registry(reg)
    return entry


def remove(name):
    """Remove a credential from the registry (does NOT delete from Keychain)."""
    reg = _load_registry()
    removed = reg.pop(name, None)
    if removed:
        _save_registry(reg)
    return removed


def add_keychain(name, service, account, password, portal=None, email=None, notes=None):
    """Store a credential in Keychain and register it.

    Deletes any existing entry for the same service+account first.
    """
    subprocess.run(
        ["security", "delete-generic-password", "-s", service, "-a", account],
        capture_output=True,
    )
    result = subprocess.run(
        ["security", "add-generic-password", "-s", service, "-a", account, "-w", password],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to store in Keychain: {result.stderr.strip()}")

    entry = {
        "type": "keychain",
        "service": service,
        "account": account,
    }
    if portal:
        entry["portal"] = portal
    if email:
        entry["email"] = email
    if notes:
        entry["notes"] = notes

    return register(name, entry)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Jarvis Credential Registry")
    sub = parser.add_subparsers(dest="action")

    sub.add_parser("list", help="List all credentials")
    sub.add_parser("audit", help="Verify all credentials are accessible")

    lookup_p = sub.add_parser("lookup", help="Look up a credential")
    lookup_p.add_argument("name")

    verify_p = sub.add_parser("verify", help="Verify a credential is accessible")
    verify_p.add_argument("name")

    hydrate_p = sub.add_parser(
        "hydrate",
        help="Pull a secret from GCP Secret Manager (via ADC) into Keychain.",
    )
    hydrate_p.add_argument("name", help="GCP Secret Manager secret name")
    hydrate_p.add_argument("--force", action="store_true",
                           help="Re-hydrate even if already in Keychain")

    sub.add_parser(
        "hydrate-all",
        help="Hydrate every secret in SECRET_TO_KEYCHAIN that is missing locally.",
    )

    args = parser.parse_args()

    if args.action == "list":
        for name, entry in sorted(list_all().items()):
            ctype = entry.get("type", "?")
            portal = entry.get("portal", entry.get("service", entry.get("path", "")))
            email = entry.get("email", "")
            print(f"  {name:25s}  type={ctype:12s}  portal={portal}")
    elif args.action == "audit":
        results = audit_all()
        ok_count = sum(1 for r in results if r["ok"])
        print(f"Audit: {ok_count}/{len(results)} credentials verified\n")
        for r in results:
            status = "OK" if r["ok"] else "FAIL"
            print(f"  [{status:4s}] {r['name']:25s}  {r['detail']}")
    elif args.action == "lookup":
        entry = lookup(args.name)
        if entry:
            print(json.dumps(entry, indent=2))
        else:
            print(f"Not found: {args.name}")
    elif args.action == "verify":
        r = verify(args.name)
        status = "OK" if r["ok"] else "FAIL"
        print(f"[{status}] {r['name']}: {r['detail']}")
    elif args.action == "hydrate":
        service = hydrate(args.name, force=args.force)
        print(f"[hydrate] Done. service={service}")
    elif args.action == "hydrate-all":
        results = hydrate_all()
        for name, status in sorted(results.items()):
            print(f"  {name}: {status}")
    else:
        parser.print_help()
