"""Who is signed in, for a Cloud Run service behind Google sign-in (Cloud Run direct IAP).

Python twin of apps/operator-console/lib/auth/identity.ts. IAP signs every request it lets
through with ``x-goog-iap-jwt-assertion``; trust the email only after that verifies against
this service's audience. Never fall back to the plain email header.
"""

from __future__ import annotations

import os

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

JWT_HEADER = "x-goog-iap-jwt-assertion"
# Google's front end drops x-goog-* headers on a request entering another service, so a
# signed-in front end that proxies to its API re-sends the assertion under this name. It is
# the same signed token and is verified the same way, so the header name carries no trust.
FORWARDED_JWT_HEADER = "x-signin-assertion"
ISSUER = "https://cloud.google.com/iap"
CERTS_URL = "https://www.gstatic.com/iap/verify/public_key-jwk"
PROJECT_NUMBER = "887772634501"
REGION = "us-central1"

_request = google_requests.Request()


class NotSignedIn(Exception):
    pass


def audience(service: str, project_number: str = PROJECT_NUMBER, region: str = REGION) -> str:
    """Direct Cloud Run IAP's JWT audience (not the load-balancer ``backendServices`` form)."""
    return f"/projects/{project_number}/locations/{region}/services/{service}"


def verify(jwt: str, aud: str, verify_token=id_token.verify_token) -> str:
    """The signed-in Google account's email, lower-cased. Raises NotSignedIn on any failure."""
    if not jwt:
        raise NotSignedIn("no Google sign-in on this request")
    try:
        claims = verify_token(jwt, _request, audience=aud, certs_url=CERTS_URL)
    except Exception as e:
        raise NotSignedIn(f"Google sign-in did not verify: {e}") from None
    if claims.get("iss") != ISSUER or not claims.get("email"):
        raise NotSignedIn("Google sign-in did not verify: wrong issuer or no email")
    return str(claims["email"]).strip().lower()


def email_from_headers(headers, aud: str) -> str:
    """``BYPASS_IAP_EMAIL`` stands in for local dev only, exactly as in the console."""
    jwt = headers.get(JWT_HEADER, "") or headers.get(FORWARDED_JWT_HEADER, "")
    if not jwt and os.environ.get("BYPASS_IAP_EMAIL"):
        return os.environ["BYPASS_IAP_EMAIL"].strip().lower()
    return verify(jwt, aud)
