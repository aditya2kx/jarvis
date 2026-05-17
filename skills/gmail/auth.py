#!/usr/bin/env python3
"""Gmail-specific OAuth helper.

The MCP server's OAuth flow only grants Drive + Sheets scopes.
Gmail requires its own OAuth flow with the gmail.modify scope.
This module handles that separately, storing tokens alongside
the MCP credentials in the same account directory.
"""

import http.server
import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import get_auth_paths, load_env

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
]

_GMAIL_CREDS_FILENAME = ".gmail-credentials.json"


def _gmail_creds_path(account):
    """Return the path where Gmail OAuth tokens are stored for a given account."""
    creds_path, _ = get_auth_paths(account)
    return os.path.join(os.path.dirname(creds_path), _GMAIL_CREDS_FILENAME)


def get_gmail_token(account="palmetto"):
    """Return a valid Gmail access token, refreshing if needed.

    If no credentials exist yet, runs a one-time browser-based OAuth flow.
    """
    creds_file = _gmail_creds_path(account)

    if os.path.exists(creds_file):
        with open(creds_file) as f:
            creds = json.load(f)
        if "refresh_token" in creds:
            return _refresh_gmail_token(account, creds["refresh_token"])

    return _run_oauth_flow(account)


def _refresh_gmail_token(account, refresh_token):
    """Use a refresh token to get a fresh access token."""
    _, env_path = get_auth_paths(account)
    env = load_env(env_path)

    data = urllib.parse.urlencode({
        "client_id": env["CLIENT_ID"],
        "client_secret": env["CLIENT_SECRET"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


def _run_oauth_flow(account):
    """One-time interactive OAuth flow — opens browser, captures redirect."""
    _, env_path = get_auth_paths(account)
    env = load_env(env_path)
    client_id = env["CLIENT_ID"]
    client_secret = env["CLIENT_SECRET"]

    redirect_uri = "http://localhost:8089"
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        })
    )

    code_holder = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code_holder["code"] = qs.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Gmail auth successful! You can close this tab.</h2>")

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("localhost", 8089), Handler)
    print(f"Opening browser for Gmail OAuth consent...")
    webbrowser.open(auth_url)
    server.handle_request()

    code = code_holder.get("code")
    if not code:
        raise RuntimeError("OAuth flow did not return an authorization code")

    token_data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=token_data)
    with urllib.request.urlopen(req) as resp:
        tokens = json.loads(resp.read())

    creds_file = _gmail_creds_path(account)
    os.makedirs(os.path.dirname(creds_file), exist_ok=True)
    with open(creds_file, "w") as f:
        json.dump(tokens, f, indent=2)
    print(f"Gmail credentials saved to {creds_file}")

    return tokens["access_token"]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gmail OAuth setup")
    parser.add_argument("--account", default="palmetto")
    args = parser.parse_args()
    token = get_gmail_token(args.account)
    print(f"Token: {token[:30]}...")
