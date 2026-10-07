#!/usr/bin/env python3
"""One-time Gmail send grant for Monthly recognition gift-card emails (Issue #369).

Runs a loopback OAuth consent for the sender account (default adi@mypalmetto.co)
with the single scope gmail.send, using the existing Google Desktop OAuth client
(~/Library/Application Support/Cursor/User/google-mcp-auth/.env CLIENT_ID/SECRET),
then stores {client_id, client_secret, refresh_token, email} as a new version of
Secret Manager secret `gmail_sender_palmetto`. The console reads that secret
(lib/recognition/gmail.ts). Run `python3 scripts/gcp_access_probe.py` first.

    python3 apps/operator-console/scripts/gmail_sender_grant.py [--email adi@mypalmetto.co]
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import webbrowser

ENV_PATH = os.path.expanduser(
    "~/Library/Application Support/Cursor/User/google-mcp-auth/.env"
)
SECRET_ID = "gmail_sender_palmetto"
PROJECT = os.environ.get("GCP_PROJECT", "jarvis-bhaga-prod")
SCOPE = "https://www.googleapis.com/auth/gmail.send"
PORT = 8091
REDIRECT_URI = f"http://localhost:{PORT}"


def _load_env(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def _consent(client_id: str, email: str) -> str:
    holder: dict[str, str | None] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            holder["code"] = qs.get("code", [None])[0]
            holder["error"] = qs.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Gmail send grant captured. You can close this tab.</h2>")

        def log_message(self, *_args) -> None:
            pass

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "login_hint": email,
        }
    )
    server = http.server.HTTPServer(("localhost", PORT), Handler)
    print(f"Open this URL as {email} if a browser does not open:\n{url}\n", flush=True)
    webbrowser.open(url)
    server.handle_request()
    if holder.get("error") or not holder.get("code"):
        raise SystemExit(f"[gmail_sender_grant] BREADCRUMB step=consent err={holder.get('error')}")
    return str(holder["code"])


def _exchange(env: dict[str, str], code: str) -> dict:
    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": env["CLIENT_ID"],
            "client_secret": env["CLIENT_SECRET"],
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _verify_sender(access_token: str) -> None:
    # gmail.send cannot read the profile; a token-info call proves the scope.
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/tokeninfo?access_token="
        + urllib.parse.quote(access_token)
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        info = json.loads(resp.read())
    if SCOPE not in info.get("scope", ""):
        raise SystemExit(f"[gmail_sender_grant] BREADCRUMB step=verify err=scope_missing got={info.get('scope')}")


def _store(payload: dict) -> None:
    exists = subprocess.run(
        ["gcloud", "secrets", "describe", SECRET_ID, f"--project={PROJECT}"],
        capture_output=True,
    ).returncode == 0
    if not exists:
        subprocess.run(
            ["gcloud", "secrets", "create", SECRET_ID, f"--project={PROJECT}",
             "--replication-policy=automatic"],
            check=True,
        )
    with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
        json.dump(payload, tmp)
        path = tmp.name
    try:
        subprocess.run(
            ["gcloud", "secrets", "versions", "add", SECRET_ID, f"--project={PROJECT}",
             f"--data-file={path}"],
            check=True,
        )
    finally:
        os.unlink(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--email", default="adi@mypalmetto.co")
    args = ap.parse_args()
    env = _load_env(ENV_PATH)
    code = _consent(env["CLIENT_ID"], args.email)
    tokens = _exchange(env, code)
    if not tokens.get("refresh_token"):
        raise SystemExit("[gmail_sender_grant] BREADCRUMB step=exchange err=no_refresh_token")
    _verify_sender(tokens["access_token"])
    _store(
        {
            "client_id": env["CLIENT_ID"],
            "client_secret": env["CLIENT_SECRET"],
            "refresh_token": tokens["refresh_token"],
            "email": args.email,
        }
    )
    print(f"Stored gmail.send grant for {args.email} in secret {SECRET_ID}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
