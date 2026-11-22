"""PINs and staff sessions for the Tasks API.

Google sign-in (skills/google_signin) decides which Google accounts reach the app. On a shared
store account, each person then signs in with a PIN and gets a short-lived signed session.
A shop device has no Google account: it enters the store's 6-digit shop code once and keeps a
signed device token. A Google account signed in through the sign-in hop gets a device token
too, so the one public link serves both. Only PIN and code hashes are stored.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

SESSION_SECONDS = 12 * 3600
MAX_PIN_MISSES = 5
LOCK_SECONDS = 300
DEVICE_SECONDS = 90 * 86400  # renewed on every bootstrap, so only an unused device runs out
GOOGLE_SECONDS = 30 * 86400  # also renewed on bootstrap; a removed account stops at its next call
MAX_CODE_MISSES = 10  # wrong shop codes from anywhere, before shop sign-in pauses
CODE_LOCK_SECONDS = 900
SHOP = "shop"  # who a shop device acts as
GOOGLE = "google"  # a device token that stands for a signed-in Google account
CREW = "crew"
MANAGER = "manager"


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_pin(pin: str, salt: str | None = None) -> dict:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
    return {"pin_salt": salt, "pin_hash": digest.hex()}


def pin_matches(pin: str, staff: dict) -> bool:
    if not staff.get("pin_hash") or not staff.get("pin_salt"):
        return False
    return hmac.compare_digest(hash_pin(pin, staff["pin_salt"])["pin_hash"], staff["pin_hash"])


def valid_pin(pin: str, role: str) -> bool:
    """Crew use 4 digits; managers 6, since a manager PIN can edit tasks and staff."""
    return pin.isdigit() and len(pin) == (6 if role == MANAGER else 4)


def _key() -> bytes:
    key = os.environ.get("STORE_TASKS_SIGNING_KEY", "")
    if len(key) < 32:
        raise AuthError("signing key not configured", 500)
    return key.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def valid_shop_code(code: str) -> bool:
    return code.isdigit() and len(code) == 6


def _sign(claims: dict) -> str:
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def sign_session(staff_id: str, store: str, role: str, account: str, now: float | None = None) -> str:
    return _sign({"sid": staff_id, "store": store, "role": role, "acct": account,
                  "exp": int((now or time.time()) + SESSION_SECONDS)})


def sign_device(store: str, gen: str, now: float | None = None) -> str:
    """``gen`` is the shop code's generation: changing the code signs every shop device out."""
    return _sign({"kind": SHOP, "store": store, "gen": gen, "exp": int((now or time.time()) + DEVICE_SECONDS)})


def sign_google(email: str, now: float | None = None) -> str:
    return _sign({"kind": GOOGLE, "email": email, "exp": int((now or time.time()) + GOOGLE_SECONDS)})


def read_session(token: str, now: float | None = None) -> dict:
    claims = _read(token, now)
    if "kind" in claims:
        raise AuthError("sign in again")
    return claims


def read_device(token: str, now: float | None = None) -> dict:
    claims = _read(token, now)
    if claims.get("kind") not in (SHOP, GOOGLE):
        raise AuthError("sign in again")
    return claims


def _read(token: str, now: float | None) -> dict:
    try:
        body, sig = token.split(".")
        want = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            raise ValueError("signature")
        claims = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        raise AuthError("sign in again") from None
    if claims.get("exp", 0) < (now or time.time()):
        raise AuthError("session expired")
    return claims
