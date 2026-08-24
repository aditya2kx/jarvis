"""Aladdin Connect client via Cognito USER_PASSWORD_AUTH + smartgarage.systems.

Mobile-app Cognito client id/secret are public (also used by Home Assistant's
AIOAladdinConnect). User email/password come from env — never committed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Optional

log = logging.getLogger("aladdin_connect")

# Public Genie Android app credentials (not operator secrets).
COGNITO_CLIENT_ID = "27iic8c3bvslqngl3hso83t74b"
COGNITO_CLIENT_SECRET = "7bokto0ep96055k42fnrmuth84k7jdcjablestb7j53o8lp63v5"
COGNITO_URL = "https://cognito-idp.us-east-2.amazonaws.com/"
API_BASE = "https://api.smartgarage.systems"

# AIOAladdinConnect / Genie: 1=open 2=opening 3=closed 4=closing
_OPEN_STATUSES = {1, 2, "1", "2", "open", "opening", "OPEN", "OPENING"}


def door_is_open(door: dict) -> bool:
    """True when Big Peach is already up (or opening) — do not send OPEN_DOOR."""
    status = door.get("status")
    if status in _OPEN_STATUSES:
        return True
    if isinstance(status, str) and status.strip().lower() in ("open", "opening"):
        return True
    return False


class AladdinError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _serial_match(door: dict, serial: str) -> bool:
    """Match configured pin against device id, serial_number, or serial prefix."""
    serial = serial.upper()
    candidates = [
        str(door.get("serial") or ""),
        str(door.get("device_id") or ""),
    ]
    for raw in candidates:
        val = raw.upper()
        if not val:
            continue
        if val == serial or val.startswith(serial) or serial.startswith(val):
            return True
    return False


def secret_hash(username: str, client_id: str = COGNITO_CLIENT_ID, client_secret: str = COGNITO_CLIENT_SECRET) -> str:
    digest = hmac.new(
        client_secret.encode("utf-8"),
        (username + client_id).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


class AladdinConnectClient:
    def __init__(self, username: str, password: str, *, dry_run: bool = True):
        self.username = username
        self.password = password
        self.dry_run = dry_run
        self._id_token = ""
        self._access_token = ""

    @classmethod
    def from_env(cls, **overrides: Any) -> "AladdinConnectClient":
        dry = overrides.get("dry_run")
        if dry is None:
            dry = os.environ.get("ALADDIN_DRY_RUN", "1") != "0"
        return cls(
            username=overrides.get("username") or os.environ.get("ALADDIN_USERNAME", ""),
            password=overrides.get("password") or os.environ.get("ALADDIN_PASSWORD", ""),
            dry_run=bool(dry),
        )

    def login(self) -> None:
        payload = {
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {
                "USERNAME": self.username,
                "PASSWORD": self.password,
                "SECRET_HASH": secret_hash(self.username),
            },
        }
        data = _cognito(payload)
        result = data.get("AuthenticationResult") or {}
        self._id_token = result.get("IdToken") or ""
        self._access_token = result.get("AccessToken") or ""
        # api.smartgarage.systems 401s IdToken; AccessToken is required.
        if not self._access_token:
            raise AladdinError("tesla-aladdin-garage fail reason=aladdin_login_no_token")

    def _token(self) -> str:
        if not self._access_token:
            self.login()
        return self._access_token

    def list_devices(self) -> list[dict]:
        data = self._api("GET", "/devices")
        return data.get("devices") or data.get("data") or []

    def list_doors(self) -> list[dict]:
        doors = []
        for device in self.list_devices():
            serial = str(device.get("serial_number") or device.get("serial") or "")
            device_id = device.get("id") or device.get("device_id")
            name = device.get("name") or ""
            owned = device.get("is_locked") is not True
            for door in device.get("doors") or []:
                doors.append(
                    {
                        "device_id": device_id,
                        "serial": serial,
                        "device_name": name,
                        "door_index": door.get("door_index", door.get("id", 1)),
                        "name": door.get("name") or name,
                        "status": door.get("status"),
                        "owned": owned,
                    }
                )
        return doors

    def resolve_door(
        self,
        *,
        serial: str = "",
        name: str = "",
        door_index: Optional[int] = None,
    ) -> dict:
        doors = self.list_doors()
        serial = (serial or "").strip().upper()
        name_l = (name or "").strip().lower()
        matches = doors
        if serial:
            matches = [d for d in matches if _serial_match(d, serial)]
        if name_l:
            named = [d for d in matches if name_l in str(d.get("name", "")).lower()]
            if named:
                matches = named
        if door_index is not None:
            indexed = [d for d in matches if int(d.get("door_index") or 0) == int(door_index)]
            if indexed:
                matches = indexed
        if not matches:
            raise AladdinError(
                f"tesla-aladdin-garage fail reason=door_not_found serial={serial} name={name}"
            )
        return matches[0]

    def open_door(self, device_id: Any, door_index: int) -> dict:
        path = f"/command/devices/{device_id}/doors/{door_index}"
        if self.dry_run:
            log.info(
                "tesla-aladdin-garage dry_run skip_open device=%s door=%s",
                device_id,
                door_index,
            )
            return {"ok": True, "dry_run": True, "device_id": device_id, "door_index": door_index}
        return self._api("POST", path, payload={"command": "OPEN_DOOR"})

    def _api(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        url = API_BASE + path
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
            "User-Agent": "okhttp/4.10.0",
        }
        raw = _http(method, url, headers=headers, payload=body)
        if not raw:
            return {"ok": True}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}


def _cognito(payload: dict) -> dict:
    headers = {
        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        "Content-Type": "application/x-amz-json-1.1",
    }
    raw = _http("POST", COGNITO_URL, headers=headers, payload=json.dumps(payload).encode())
    return json.loads(raw)


def _http(method: str, url: str, headers: Optional[dict] = None, payload: Optional[bytes] = None) -> str:
    req = urllib.request.Request(url, data=payload, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        log.error("tesla-aladdin-garage fail reason=aladdin_http status=%s url=%s body=%s", e.code, url, raw[:400])
        raise AladdinError(f"HTTP {e.code} {url}", status=e.code, body=raw) from e


secret_hash = secret_hash
AladdinConnectClient = AladdinConnectClient
AladdinError = AladdinError
AladdinConnectClient.resolve_door = AladdinConnectClient.resolve_door
AladdinConnectClient.open_door = AladdinConnectClient.open_door
AladdinConnectClient.list_doors = AladdinConnectClient.list_doors
AladdinConnectClient._api = AladdinConnectClient._api
