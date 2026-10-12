#!/usr/bin/env python3
"""GCP from a Cursor Cloud Agent VM with no static credential.

    python3 scripts/cloud_agent_gcp.py secret github-bot-pat     # secret value on stdout
    python3 scripts/cloud_agent_gcp.py id-token                  # IAP ID token (console previews)

Cursor OIDC token (local agent socket) → GCP STS federated token → service account
cursor-cloud-agent (access token / ID token). Trust is set up once by
scripts/provision_cloud_agent_identity.sh (docs/contributing/gcp-access.md recipe F).
"""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

PROJECT = "jarvis-bhaga-prod"
AGENT_SA = f"cursor-cloud-agent@{PROJECT}.iam.gserviceaccount.com"
AUDIENCE = (
    "//iam.googleapis.com/projects/887772634501/locations/global/"
    "workloadIdentityPools/cursor-agents/providers/cursor"
)
# OAuth client behind the Operator Console's IAP (prod and previews).
IAP_CLIENT_ID = "887772634501-rtccq8d5foru6kgcqbi3a1n6kmcqn2eu.apps.googleusercontent.com"
SOCKET = os.environ.get("CURSOR_AGENT_SOCKET") or "/run/cursor/api.sock"
IAM_CREDENTIALS = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{AGENT_SA}"


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__("localhost", timeout=10)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(self._path)


def cursor_oidc_token(aud: str = AUDIENCE, sock: str = SOCKET) -> str:
    conn = _UnixHTTPConnection(sock)
    conn.request("POST", "/v1/tokens/oidc", json.dumps({"aud": aud}), {"Content-Type": "application/json"})
    body = json.loads(conn.getresponse().read().decode() or "{}")
    if not body.get("token"):
        raise RuntimeError(f"Cursor OIDC: {body.get('error') or 'no token'}")
    return body["token"]


def _request(url: str, *, data: bytes | None = None, token: str | None = None, form: bool = False) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def federated_access_token(oidc: str) -> str:
    return _request("https://sts.googleapis.com/v1/token", form=True, data=urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "audience": AUDIENCE,
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "subject_token": oidc,
    }).encode())["access_token"]


def sa_access_token(federated: str) -> str:
    body = json.dumps({"scope": ["https://www.googleapis.com/auth/cloud-platform"]}).encode()
    return _request(f"{IAM_CREDENTIALS}:generateAccessToken", data=body, token=federated)["accessToken"]


def sa_id_token(federated: str, audience: str = IAP_CLIENT_ID) -> str:
    body = json.dumps({"audience": audience, "includeEmail": True}).encode()
    return _request(f"{IAM_CREDENTIALS}:generateIdToken", data=body, token=federated)["token"]


def access_secret(name: str, token: str, project: str = PROJECT) -> str:
    url = f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{name}/versions/latest:access"
    return base64.b64decode(_request(url, token=token)["payload"]["data"]).decode()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("secret").add_argument("name")
    sub.add_parser("id-token").add_argument("--audience", default=IAP_CLIENT_ID)
    a = ap.parse_args(argv)
    try:
        federated = federated_access_token(cursor_oidc_token())
        if a.cmd == "secret":
            sys.stdout.write(access_secret(a.name, sa_access_token(federated)))
        else:
            sys.stdout.write(sa_id_token(federated, a.audience))
    except (OSError, RuntimeError, KeyError) as e:
        detail = e.read().decode(errors="replace")[:200] if isinstance(e, urllib.error.HTTPError) else e
        print(f"cloud_agent_gcp: {a.cmd} {getattr(a, 'name', '')} unavailable — {detail}".replace("  ", " "), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
