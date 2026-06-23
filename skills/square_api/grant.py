#!/usr/bin/env python3
"""skills/square_api/grant — One-time interactive OAuth authorization-code flow.

Run once on the operator's laptop to capture a Square OAuth token pair and
store it in Secret Manager (or Keychain for local use). After this runs,
the nightly Cloud Run job refreshes the access token automatically via auth.py.

Usage:
    python3 -m skills.square_api.grant --store palmetto

Steps:
    1. Opens the Square OAuth authorize URL in the system browser.
    2. Starts a local HTTP server on port 8731 to catch the redirect.
    3. Exchanges the authorization code for access + refresh tokens.
    4. Stores the result in Secret Manager (BHAGA_SECRETS_BACKEND=gcp) or
       Keychain (default).

Prerequisites:
    - In https://developer.squareup.com/apps → app → OAuth → Production:
      set Redirect URL to http://localhost:8731/callback
    - Your ADC must be set up: gcloud auth application-default login
    - The app's Application Secret must be supplied via --app-secret or
      read from an existing OAuth secret entry.
"""

from __future__ import annotations

import argparse
import getpass
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.square_api.auth import (
    _post_token,
    save_oauth_secret,
    api_base,
)

REDIRECT_PORT = 8731
REDIRECT_PATH = "/callback"
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}{REDIRECT_PATH}"
OAUTH_SCOPES = (
    "MERCHANT_PROFILE_READ PAYMENTS_READ ORDERS_READ ITEMS_READ "
    "EMPLOYEES_READ REPORTING_READ"
)
# Production Application ID for the Jarvis Square app.
APP_ID = "sq0idp-Hcto2eTRUUFBAyhRtxCSGg"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-shot HTTP handler that captures the OAuth callback code."""

    _code: str | None = None
    _state: str | None = None
    _error: str | None = None
    _done: threading.Event = threading.Event()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != REDIRECT_PATH:
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        type(self)._code = (qs.get("code") or [None])[0]
        type(self)._state = (qs.get("state") or [None])[0]
        type(self)._error = (qs.get("error") or [None])[0]
        body = (
            b"<html><body><h2>Square OAuth callback received.</h2>"
            b"<p>Authorization complete. You can close this tab.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        type(self)._done.set()

    def log_message(self, *args: object) -> None:  # silence request logs
        pass


def _build_authorize_url(app_id: str, state: str) -> str:
    scopes_enc = urllib.parse.quote(OAUTH_SCOPES)
    redirect_enc = urllib.parse.quote(REDIRECT_URI)
    return (
        f"{api_base()}/oauth2/authorize"
        f"?client_id={app_id}"
        f"&scope={scopes_enc}"
        f"&session=false"
        f"&state={state}"
        f"&redirect_uri={redirect_enc}"
    )


def run_grant(store: str = "palmetto", app_id: str = APP_ID, app_secret: str = "") -> dict:
    """Interactive OAuth grant flow. Returns the stored secret dict.

    Opens a browser, listens on localhost:8731, exchanges the code, and
    writes the token pair to Secret Manager or Keychain.
    """
    if not app_secret:
        raise SystemExit(
            "Application Secret is required. Supply via --app-secret "
            "(find it at developer.squareup.com/apps → app → OAuth page)."
        )

    state = secrets.token_hex(16)
    authorize_url = _build_authorize_url(app_id, state)

    _CallbackHandler._code = None
    _CallbackHandler._state = None
    _CallbackHandler._error = None
    _CallbackHandler._done.clear()

    server = http.server.HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"\n[square_api.grant] Opening browser for Square OAuth…")
    print(f"  If the browser does not open, paste this URL:\n  {authorize_url}\n")
    webbrowser.open(authorize_url)
    print(f"[square_api.grant] Waiting for callback on port {REDIRECT_PORT} (timeout 5 min)…")

    if not _CallbackHandler._done.wait(timeout=300):
        server.shutdown()
        raise SystemExit("Timed out waiting for OAuth callback (5 minutes).")

    server.shutdown()

    if _CallbackHandler._error:
        raise SystemExit(f"Square returned an authorization error: {_CallbackHandler._error}")

    code = _CallbackHandler._code
    received_state = _CallbackHandler._state
    if not code:
        raise SystemExit("Callback received but no authorization code present.")
    if received_state != state:
        raise SystemExit(
            f"CSRF state mismatch (expected={state!r}, got={received_state!r}). Aborting."
        )

    print("[square_api.grant] Authorization code received. Exchanging for tokens…")
    resp = _post_token(
        {},
        {
            "client_id": app_id,
            "client_secret": app_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
    )

    access_token = resp.get("access_token")
    refresh_token = resp.get("refresh_token")
    expires_at = resp.get("expires_at", "")
    merchant_id = resp.get("merchant_id", "")

    if not access_token or not refresh_token:
        raise SystemExit(
            f"Token exchange failed — missing access_token or refresh_token.\n"
            f"Square response: {json.dumps(resp)[:400]}"
        )

    secret_data: dict = {
        "application_id": app_id,
        "application_secret": app_secret,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "merchant_id": merchant_id,
    }
    save_oauth_secret(store, secret_data)
    print(
        f"\n[square_api.grant] SUCCESS — stored Square OAuth secret for store={store!r}.\n"
        f"  merchant_id={merchant_id}\n"
        f"  expires_at={expires_at}\n"
        f"\n  Next step: run `gcloud secrets add-iam-policy-binding square_{store}_oauth \\\n"
        f"    --project jarvis-bhaga-prod \\\n"
        f"    --member serviceAccount:bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com \\\n"
        f"    --role roles/secretmanager.secretVersionAdder` once if not done.\n"
    )
    return secret_data


if __name__ == "__main__":
    cli = argparse.ArgumentParser(
        description="Square OAuth grant helper (run once on operator laptop)"
    )
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--app-id", default=APP_ID, help="Square Application ID")
    cli.add_argument("--app-secret", default=None, help="Square Application Secret")
    args = cli.parse_args()

    if not args.app_secret:
        # Try to read from an existing partial secret entry
        try:
            from skills.square_api.auth import load_oauth_secret as _load
            existing = _load(args.store)
            args.app_secret = existing.get("application_secret") or ""
            if args.app_secret:
                print("[square_api.grant] Read app_secret from existing OAuth secret entry.")
        except Exception:
            pass

    if not args.app_secret:
        args.app_secret = getpass.getpass(
            "Square Application Secret (from developer.squareup.com OAuth page): "
        ).strip()

    run_grant(store=args.store, app_id=args.app_id, app_secret=args.app_secret)
