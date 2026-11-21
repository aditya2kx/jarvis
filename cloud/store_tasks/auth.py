"""PINs and staff sessions for the Tasks API.

Google sign-in (skills/google_signin) decides which Google accounts reach the app. On a shared
store account, each person then signs in with a PIN and gets a short-lived signed session.
Only PIN hashes are stored.
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


def sign_session(staff_id: str, store: str, role: str, account: str, now: float | None = None) -> str:
    body = _b64(json.dumps({
        "sid": staff_id, "store": store, "role": role, "acct": account,
        "exp": int((now or time.time()) + SESSION_SECONDS),
    }, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_session(token: str, now: float | None = None) -> dict:
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
