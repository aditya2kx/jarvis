"""Tesla Fleet API client (urllib). Location read only — never wake_up / commands."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

log = logging.getLogger("tesla_fleet")

DEFAULT_TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
DEFAULT_AUTH_URL = "https://auth.tesla.com/oauth2/v3/authorize"
DEFAULT_AUDIENCE = "https://fleet-api.prd.na.vn.cloud.tesla.com"
DEFAULT_SCOPES = "openid offline_access user_data vehicle_device_data vehicle_location"
PUBLIC_KEY_PATH = "/.well-known/appspecific/com.tesla.3p.public-key.pem"

# Fleet vehicle_data rejects comma-separated endpoints (HTTP 400). Must be ';'.
LOCATION_ENDPOINTS = "location_data;drive_state"


def fleet_telemetry_config_body(
    *,
    vins: list[str],
    hostname: str,
    port: int = 443,
    ca: str = "",
    interval_seconds: int = 15,
    minimum_delta: float = 80.0,
) -> dict:
    """JSON body for POST /api/1/vehicles/fleet_telemetry_config.

    Cars connect to `hostname:port` over mTLS (not Cloud Run). `minimum_delta`
    is metres between Location publishes. Does not wake a sleeping vehicle.
    """
    host = hostname.strip().removeprefix("https://").removeprefix("http://").split("/")[0]
    config: dict[str, Any] = {
        "hostname": host,
        "port": int(port),
        "fields": {
            "Location": {
                "interval_seconds": int(interval_seconds),
                "minimum_delta": float(minimum_delta),
            }
        },
    }
    if ca.strip():
        config["ca"] = ca.strip()
    return {"vins": [v.strip().upper() for v in vins if v.strip()], "config": config}


class TeslaFleetError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def vehicle_data_path(vehicle_id: str | int, endpoints: str = LOCATION_ENDPOINTS) -> str:
    """Build /vehicle_data path. `endpoints` must be semicolon-separated."""
    if "," in endpoints:
        raise TeslaFleetError(
            "vehicle_data endpoints must be semicolon-separated "
            f"(got comma in {endpoints!r})"
        )
    q = urllib.parse.urlencode({"endpoints": endpoints})
    return f"/api/1/vehicles/{vehicle_id}/vehicle_data?{q}"


def parse_vehicle_location(resp: dict) -> dict:
    """Pull lat/lon from a vehicle_data payload (drive_state, else location_data)."""
    drive = resp.get("drive_state") or {}
    loc = resp.get("location_data") or {}
    lat = drive.get("latitude") if drive.get("latitude") is not None else loc.get("latitude")
    lon = drive.get("longitude") if drive.get("longitude") is not None else loc.get("longitude")
    return {
        "latitude": lat,
        "longitude": lon,
        "heading": drive.get("heading"),
        "speed": drive.get("speed"),
        "shift_state": drive.get("shift_state"),
        "power": drive.get("power"),
        "state": resp.get("state"),
        "raw": resp,
    }


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class TeslaFleetClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        audience: str = DEFAULT_AUDIENCE,
        token_url: str = DEFAULT_TOKEN_URL,
        auth_url: str = DEFAULT_AUTH_URL,
        redirect_uri: str = "",
        partner_domain: str = "",
        refresh_token: str = "",
        on_tokens: Optional[Callable[[dict], None]] = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.audience = audience.rstrip("/")
        self.token_url = token_url
        self.auth_url = auth_url
        self.redirect_uri = redirect_uri
        self.partner_domain = partner_domain
        self._refresh_token = refresh_token
        self._access_token = ""
        self._access_exp = 0.0
        self.on_tokens = on_tokens
        self._pkce_verifier = ""
        self._oauth_state = ""

    @classmethod
    def from_env(cls, **overrides: Any) -> "TeslaFleetClient":
        return cls(
            client_id=overrides.get("client_id") or os.environ.get("TESLA_CLIENT_ID", ""),
            client_secret=overrides.get("client_secret")
            or os.environ.get("TESLA_CLIENT_SECRET", ""),
            audience=overrides.get("audience")
            or os.environ.get("TESLA_AUDIENCE", DEFAULT_AUDIENCE),
            token_url=overrides.get("token_url")
            or os.environ.get("TESLA_TOKEN_URL", DEFAULT_TOKEN_URL),
            auth_url=overrides.get("auth_url")
            or os.environ.get("TESLA_AUTH_URL", DEFAULT_AUTH_URL),
            redirect_uri=overrides.get("redirect_uri")
            or os.environ.get("TESLA_REDIRECT_URI", ""),
            partner_domain=overrides.get("partner_domain")
            or os.environ.get("TESLA_PARTNER_DOMAIN", ""),
            refresh_token=overrides.get("refresh_token")
            or os.environ.get("TESLA_REFRESH_TOKEN", ""),
            on_tokens=overrides.get("on_tokens"),
        )

    def needs_user_auth(self) -> bool:
        return not (self.client_id and (self._refresh_token or self._access_token))

    def public_key_url(self) -> str:
        domain = self.partner_domain.strip().rstrip("/")
        if domain.startswith("http"):
            return domain + PUBLIC_KEY_PATH
        return f"https://{domain}{PUBLIC_KEY_PATH}"

    def fetch_hosted_public_key(self) -> str:
        url = self.public_key_url()
        pem = _http_json("GET", url, expect_json=False)
        if "BEGIN PUBLIC KEY" not in pem:
            raise TeslaFleetError(f"tesla-aladdin-garage fail reason=bad_public_key url={url}")
        return pem

    def authorization_url(self) -> str:
        if not self.redirect_uri:
            raise TeslaFleetError("TESLA_REDIRECT_URI is required for authorize")
        self._pkce_verifier = _b64url(secrets.token_bytes(32))
        challenge = _b64url(hashlib.sha256(self._pkce_verifier.encode("ascii")).digest())
        self._oauth_state = secrets.token_urlsafe(24)
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": os.environ.get("TESLA_SCOPES", DEFAULT_SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": self._oauth_state,
            "prompt": "login",
        }
        return f"{self.auth_url}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, state: str = "") -> dict:
        if state and self._oauth_state and state != self._oauth_state:
            raise TeslaFleetError("OAuth state mismatch")
        body = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
            "audience": self.audience,
            "code_verifier": self._pkce_verifier,
        }
        return self._store_token(self._post_form(self.token_url, body))

    def client_credentials_token(self) -> str:
        body = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "audience": self.audience,
            "scope": "openid",
        }
        data = self._post_form(self.token_url, body)
        return data["access_token"]

    def ensure_access_token(self) -> str:
        if self._access_token and time.time() < self._access_exp - 60:
            return self._access_token
        if not self._refresh_token:
            raise TeslaFleetError(
                "tesla-aladdin-garage fail reason=missing_refresh_token "
                "action=reauthorize"
            )
        body = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": self._refresh_token,
            "audience": self.audience,
        }
        # Some Tesla apps require client_secret on refresh.
        if self.client_secret:
            body["client_secret"] = self.client_secret
        return self._store_token(self._post_form(self.token_url, body))["access_token"]

    def register_partner(self) -> dict:
        token = self.client_credentials_token()
        payload = json.dumps({"domain": self.partner_domain}).encode()
        return self._api("POST", "/api/1/partner_accounts", token=token, payload=payload)

    def verify_partner_key(self) -> dict:
        token = self.client_credentials_token()
        q = urllib.parse.urlencode({"domain": self.partner_domain})
        return self._api("GET", f"/api/1/partner_accounts/public_key?{q}", token=token)

    def list_vehicles(self) -> list[dict]:
        data = self._api("GET", "/api/1/vehicles")
        return data.get("response") or []

    def find_vehicle(self, vin: str) -> dict:
        vin = vin.strip().upper()
        for v in self.list_vehicles():
            if str(v.get("vin", "")).upper() == vin:
                return v
        raise TeslaFleetError(f"tesla-aladdin-garage fail reason=vin_not_found vin={vin}")

    def vehicle_location(self, vehicle_id: str | int) -> dict:
        """Return drive/location fields. Does not wake a sleeping vehicle."""
        path = vehicle_data_path(vehicle_id)
        data = self._api("GET", path)
        return parse_vehicle_location(data.get("response") or {})

    def put_fleet_telemetry_config(
        self,
        *,
        vins: list[str],
        hostname: str,
        port: int = 443,
        ca: str = "",
        interval_seconds: int = 15,
        minimum_delta: float = 80.0,
    ) -> dict:
        """Ask Tesla to stream Location to a fleet-telemetry host. Never wake_up.

        Tesla requires this POST through the Vehicle Command HTTP Proxy
        (unsigned Fleet API returns HTTP 400).
        """
        body = fleet_telemetry_config_body(
            vins=vins,
            hostname=hostname,
            port=port,
            ca=ca,
            interval_seconds=interval_seconds,
            minimum_delta=minimum_delta,
        )
        return self._api(
            "POST",
            "/api/1/vehicles/fleet_telemetry_config",
            payload=json.dumps(body).encode(),
        )

    def _store_token(self, data: dict) -> dict:
        self._access_token = data.get("access_token") or ""
        expires_in = int(data.get("expires_in") or 3600)
        self._access_exp = time.time() + expires_in
        if data.get("refresh_token"):
            self._refresh_token = data["refresh_token"]
        if self.on_tokens:
            self.on_tokens(
                {
                    "access_token": self._access_token,
                    "refresh_token": self._refresh_token,
                    "expires_in": expires_in,
                }
            )
        return data

    def _api(self, method: str, path: str, token: Optional[str] = None, payload: Optional[bytes] = None) -> dict:
        if token is None:
            token = self.ensure_access_token()
        url = self.audience + path if path.startswith("/") else path
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        return json.loads(_http_json(method, url, headers=headers, payload=payload))

    def _post_form(self, url: str, body: dict) -> dict:
        encoded = urllib.parse.urlencode(body).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        return json.loads(_http_json("POST", url, headers=headers, payload=encoded))


def _http_json(
    method: str,
    url: str,
    headers: Optional[dict] = None,
    payload: Optional[bytes] = None,
    expect_json: bool = True,
) -> str:
    req = urllib.request.Request(url, data=payload, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        log.error("tesla-aladdin-garage fail reason=http status=%s url=%s body=%s", e.code, url, raw[:400])
        raise TeslaFleetError(f"HTTP {e.code} {url}", status=e.code, body=raw) from e
    except urllib.error.URLError as e:
        log.error("tesla-aladdin-garage fail reason=network url=%s err=%s", url, e)
        raise TeslaFleetError(f"network {url}: {e}") from e


# Names used by the garage worker / tests / __init__
TeslaFleetClient = TeslaFleetClient
TeslaFleetError = TeslaFleetError
vehicle_data_path = vehicle_data_path
_http_json = _http_json
TeslaFleetClient.needs_user_auth = TeslaFleetClient.needs_user_auth
TeslaFleetClient.find_vehicle = TeslaFleetClient.find_vehicle
TeslaFleetClient.vehicle_location = TeslaFleetClient.vehicle_location
TeslaFleetClient.fetch_hosted_public_key = TeslaFleetClient.fetch_hosted_public_key
TeslaFleetClient.verify_partner_key = TeslaFleetClient.verify_partner_key
TeslaFleetClient.register_partner = TeslaFleetClient.register_partner
TeslaFleetClient.list_vehicles = TeslaFleetClient.list_vehicles
TeslaFleetClient.authorization_url = TeslaFleetClient.authorization_url
TeslaFleetClient.exchange_code = TeslaFleetClient.exchange_code
parse_vehicle_location = parse_vehicle_location
