#!/usr/bin/env python3
"""Read a Secret Manager secret from a Cursor Cloud Agent VM with no static credential.

    python3 scripts/cloud_agent_secret.py github-bot-pat   # value on stdout

Cursor OIDC token (local agent socket) → GCP STS federated token → Secret Manager. Trust is set up
once by scripts/provision_cloud_agent_identity.sh (docs/contributing/gcp-access.md recipe F).
"""
from __future__ import annotations

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
AUDIENCE = (
    "//iam.googleapis.com/projects/887772634501/locations/global/"
    "workloadIdentityPools/cursor-agents/providers/cursor"
)
SOCKET = os.environ.get("CURSOR_AGENT_SOCKET") or "/run/cursor/api.sock"


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


def _post_form(url: str, fields: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def federated_access_token(oidc: str) -> str:
    return _post_form("https://sts.googleapis.com/v1/token", {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "audience": AUDIENCE,
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "subject_token": oidc,
    })["access_token"]


def access_secret(name: str, token: str, project: str = PROJECT) -> str:
    req = urllib.request.Request(
        f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{name}/versions/latest:access",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return base64.b64decode(json.loads(r.read().decode())["payload"]["data"]).decode()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    try:
        sys.stdout.write(access_secret(argv[0], federated_access_token(cursor_oidc_token())))
    except (OSError, RuntimeError, KeyError, urllib.error.HTTPError) as e:
        detail = e.read().decode(errors="replace")[:200] if isinstance(e, urllib.error.HTTPError) else e
        print(f"cloud_agent_secret: {argv[0]} unavailable — {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
