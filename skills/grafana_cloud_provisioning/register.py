#!/usr/bin/env python3
"""Grafana Cloud registration — BigQuery datasource + dashboard push.

After provisioning (signup + API token captured in Keychain):
  1. configure_bigquery_datasource() — calls Grafana API to create/update the
     BigQuery datasource using the read-only SA key from Secret Manager.
  2. push_dashboard(dashboard_json) — upserts a dashboard JSON to Grafana Cloud.
  3. get_dashboard_url(uid) — returns the shareable public URL for a dashboard.

Runtime: fully cloud-to-cloud.  The laptop only runs this once at provision
time; the dashboard JSON is in-repo and pushed by a GitHub Action on merge.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import urllib.error
import urllib.request

_KEYCHAIN_SERVICE = "grafana-cloud-api-token"


def _get_token(org_slug: str) -> str:
    """Resolve the Grafana Cloud API token (GRAFANA_API_TOKEN env var, then Keychain)."""
    from skills.grafana_cloud_provisioning.provision import get_api_token
    token = get_api_token(org_slug)
    if not token:
        raise RuntimeError(
            "Grafana Cloud API token not found. Set the GRAFANA_API_TOKEN env "
            "var (the repo secret used by .github/workflows/grafana-dashboard-sync.yml) "
            f"or, locally, store it in Keychain (service={_KEYCHAIN_SERVICE}, "
            f"account={org_slug}) via the signup_playbook."
        )
    return token


def _api(
    method: str,
    url: str,
    *,
    token: str,
    body: dict | None = None,
) -> dict:
    """Call the Grafana HTTP API."""
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        raise RuntimeError(
            f"Grafana API {method} {url} → HTTP {e.code}: {body_bytes.decode()[:500]}"
        ) from None


def configure_bigquery_datasource(
    *,
    org_slug: str,
    gcp_project: str = "jarvis-bhaga-prod",
    bq_dataset: str = "bhaga",
    sa_email: str = "grafana-bq-reader@jarvis-bhaga-prod.iam.gserviceaccount.com",
    sa_key_secret: str = "grafana-bq-reader-key",
    gcp_secret_project: str = "jarvis-bhaga-prod",
) -> dict:
    """Create or update the BigQuery datasource in Grafana Cloud.

    Reads the service-account JSON key from GCP Secret Manager (the key was
    minted by create_read_only_sa() and stored there).  This call is
    cloud-to-cloud — no key files on disk.

    Args:
        org_slug: Grafana Cloud org slug (e.g. 'bhaga-palmetto').
        gcp_project: GCP project hosting BigQuery.
        bq_dataset: Default BigQuery dataset.
        sa_email: Read-only SA email.
        sa_key_secret: Secret Manager secret name holding the SA JSON key.
        gcp_secret_project: GCP project hosting the secret.

    Returns:
        Grafana API response dict, augmented with a top-level ``uid`` key holding
        the datasource's canonical UID (so the deploy step can bind panels to it).
    """
    token = _get_token(org_slug)

    # Read SA key from Secret Manager
    result = subprocess.run(
        ["gcloud", "secrets", "versions", "access", "latest",
         f"--secret={sa_key_secret}",
         f"--project={gcp_secret_project}"],
        capture_output=True, text=True, check=True,
    )
    sa_key_json = result.stdout.strip()
    sa_key = json.loads(sa_key_json)

    datasource_payload = {
        "name": "BHAGA BigQuery",
        "type": "grafana-bigquery-datasource",
        "access": "proxy",
        "isDefault": True,
        "jsonData": {
            "authenticationType": "jwt",
            "clientEmail": sa_key["client_email"],
            "defaultProject": gcp_project,
            "tokenUri": sa_key["token_uri"],
            "processingLocation": "US",
        },
        "secureJsonData": {
            "privateKey": sa_key["private_key"],
        },
    }

    grafana_url = f"https://{org_slug}.grafana.net"
    # Check if datasource already exists
    try:
        existing = _api("GET", f"{grafana_url}/api/datasources/name/BHAGA%20BigQuery",
                        token=token)
        ds_id = existing["id"]
        print(f"[grafana] Updating existing datasource id={ds_id}")
        resp = _api("PUT", f"{grafana_url}/api/datasources/{ds_id}",
                    token=token, body=datasource_payload)
    except RuntimeError as e:
        if "404" in str(e):
            print("[grafana] Creating new datasource")
            resp = _api("POST", f"{grafana_url}/api/datasources",
                        token=token, body=datasource_payload)
        else:
            raise

    # Resolve the canonical UID. Panels bind to this at deploy time instead of
    # the datasource *name*, which Grafana cannot resolve from a `uid:` field.
    uid = (resp.get("datasource") or {}).get("uid") or resp.get("uid")
    if not uid:
        ds = _api("GET", f"{grafana_url}/api/datasources/name/BHAGA%20BigQuery",
                  token=token)
        uid = ds.get("uid")
    if uid:
        print(f"[grafana] Datasource uid={uid}")
    return {**resp, "uid": uid}


def get_bigquery_datasource_uid(
    *, org_slug: str, name: str = "BHAGA BigQuery"
) -> str | None:
    """Return the UID of the BigQuery datasource by name, or None if absent.

    Used by the deploy step when only the dashboard is pushed (so it can still
    bind panels to the real UID without reconfiguring the datasource).
    """
    import urllib.parse

    token = _get_token(org_slug)
    grafana_url = f"https://{org_slug}.grafana.net"
    quoted = urllib.parse.quote(name)
    try:
        ds = _api("GET", f"{grafana_url}/api/datasources/name/{quoted}", token=token)
        return ds.get("uid")
    except RuntimeError as e:
        if "404" in str(e):
            return None
        raise


def push_dashboard(
    dashboard_json: dict,
    *,
    org_slug: str,
    folder_title: str = "BHAGA Analytics",
    overwrite: bool = True,
) -> dict:
    """Upsert a dashboard JSON to Grafana Cloud.

    Args:
        dashboard_json: The Grafana dashboard model (the 'dashboard' key content).
        org_slug: Grafana Cloud org slug.
        folder_title: Dashboard folder (created if not exists).
        overwrite: Replace existing dashboard with same uid.

    Returns:
        Grafana API response with 'url' key for the shareable link.
    """
    token = _get_token(org_slug)
    grafana_url = f"https://{org_slug}.grafana.net"

    # Ensure folder exists
    folder_uid = _ensure_folder(grafana_url, token, folder_title)

    payload = {
        "dashboard": dashboard_json,
        "folderUid": folder_uid,
        "overwrite": overwrite,
        "message": "Deployed by jarvis-agent via Grafana API",
    }
    result = _api("POST", f"{grafana_url}/api/dashboards/db",
                  token=token, body=payload)
    url = f"{grafana_url}{result.get('url', '')}"
    print(f"[grafana] Dashboard deployed: {url}")
    return {**result, "full_url": url}


def _ensure_folder(grafana_url: str, token: str, title: str) -> str:
    """Return the uid of a Grafana folder, creating it if needed."""
    folders = _api("GET", f"{grafana_url}/api/folders", token=token)
    for f in folders:
        if f.get("title") == title:
            return f["uid"]
    created = _api("POST", f"{grafana_url}/api/folders",
                   token=token, body={"title": title})
    return created["uid"]


def get_dashboard_url(uid: str, *, org_slug: str) -> str:
    """Return the shareable Grafana dashboard URL for a given dashboard uid."""
    token = _get_token(org_slug)
    grafana_url = f"https://{org_slug}.grafana.net"
    result = _api("GET", f"{grafana_url}/api/dashboards/uid/{uid}", token=token)
    meta = result.get("meta", {})
    slug = meta.get("slug", uid)
    return f"{grafana_url}/d/{uid}/{slug}"


def create_read_only_sa(
    *,
    gcp_project: str = "jarvis-bhaga-prod",
    bq_dataset: str = "bhaga",
    sa_name: str = "grafana-bq-reader",
    secret_name: str = "grafana-bq-reader-key",
) -> str:
    """Create the read-only BigQuery SA for Grafana and store its key in Secret Manager.

    Idempotent: if the SA already exists, skip creation.  If the Secret Manager
    secret already exists, add a new version.

    Returns the SA email.
    """
    sa_email = f"{sa_name}@{gcp_project}.iam.gserviceaccount.com"

    # Create SA (idempotent)
    existing = subprocess.run(
        ["gcloud", "iam", "service-accounts", "describe", sa_email,
         f"--project={gcp_project}"],
        capture_output=True,
    )
    if existing.returncode != 0:
        print(f"[grafana] Creating service account {sa_email}")
        subprocess.run(
            ["gcloud", "iam", "service-accounts", "create", sa_name,
             f"--display-name=Grafana BQ Reader (read-only)",
             f"--project={gcp_project}"],
            check=True,
        )
    else:
        print(f"[grafana] Service account {sa_email} already exists")

    # Grant BigQuery Data Viewer + Job User on dataset
    for role in ("roles/bigquery.dataViewer", "roles/bigquery.jobUser"):
        subprocess.run(
            ["gcloud", "projects", "add-iam-policy-binding", gcp_project,
             f"--member=serviceAccount:{sa_email}",
             f"--role={role}",
             "--condition=None"],
            check=True, capture_output=True,
        )
    print(f"[grafana] Granted bigquery.dataViewer + jobUser to {sa_email}")

    # Create SA key and store in Secret Manager
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        key_path = f.name
    try:
        subprocess.run(
            ["gcloud", "iam", "service-accounts", "keys", "create", key_path,
             f"--iam-account={sa_email}", f"--project={gcp_project}"],
            check=True,
        )
        key_bytes = pathlib.Path(key_path).read_bytes()
    finally:
        os.unlink(key_path)

    # Store in Secret Manager (create or add version)
    sm_result = subprocess.run(
        ["gcloud", "secrets", "describe", secret_name, f"--project={gcp_project}"],
        capture_output=True,
    )
    if sm_result.returncode != 0:
        subprocess.run(
            ["gcloud", "secrets", "create", secret_name,
             f"--project={gcp_project}", "--replication-policy=automatic"],
            check=True,
        )
    subprocess.run(
        ["gcloud", "secrets", "versions", "add", secret_name,
         f"--project={gcp_project}", "--data-file=-"],
        input=key_bytes, check=True,
    )
    print(f"[grafana] SA key stored in Secret Manager secret '{secret_name}'")

    return sa_email
