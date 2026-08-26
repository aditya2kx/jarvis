"""Signed fleet_telemetry_config via GCE tesla-http-proxy (IAP SSH).

Cloud Run cannot reach 127.0.0.1:4443. IAP TCP tunnel to :4443 also fails
because the proxy binds localhost only. This helper SCPs a body+token onto
tesla-fleet-telemetry and curls the proxy there. Never prints secrets.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from google.cloud import secretmanager

from skills.tesla_fleet.client import TeslaFleetClient, fleet_telemetry_config_body

PROJECT = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "jarvis-bhaga-prod"
ZONE = os.environ.get("GARAGE_TELEMETRY_ZONE", "us-central1-a")
INSTANCE = os.environ.get("GARAGE_TELEMETRY_INSTANCE", "tesla-fleet-telemetry")
REMOTE_DIR = "/tmp/garage-proxy-cfg"


def _secret(name: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    path = f"projects/{PROJECT}/secrets/{name}/versions/latest"
    return client.access_secret_version(request={"name": path}).payload.data.decode().strip()


def _gcloud(*args: str) -> None:
    subprocess.check_call(["gcloud", *args], stdout=subprocess.DEVNULL)


def _gcloud_out(*args: str) -> str:
    return subprocess.check_output(["gcloud", *args], text=True)


def main() -> int:
    os.environ["TESLA_CLIENT_ID"] = _secret("tesla-fleet-client-id")
    os.environ["TESLA_CLIENT_SECRET"] = _secret("tesla-fleet-client-secret")
    os.environ["TESLA_REFRESH_TOKEN"] = _secret("tesla-fleet-refresh-token")
    ca = _secret("tesla-telemetry-ca")
    tesla = TeslaFleetClient.from_env()
    if tesla.needs_user_auth():
        print("ok=false reason=needs_reauth", file=sys.stderr)
        return 2
    token = tesla.ensure_access_token()
    body = fleet_telemetry_config_body(
        vins=[os.environ.get("TESLA_VIN", "7SAYGAEE2TF605512")],
        hostname=os.environ.get("TESLA_TELEMETRY_HOST", "35.239.192.226.sslip.io"),
        port=int(os.environ.get("TESLA_TELEMETRY_PORT", "8443") or 8443),
        ca=ca,
        interval_seconds=15,
        minimum_delta=float(os.environ.get("LOCATION_MIN_DELTA_M", "80") or 80),
    )
    td = Path(tempfile.mkdtemp(prefix="garage-proxy-"))
    (td / "body.json").write_text(json.dumps(body))
    (td / "token").write_text(token)
    os.chmod(td / "body.json", 0o600)
    os.chmod(td / "token", 0o600)
    iap = ["--tunnel-through-iap", "--zone", ZONE, "--project", PROJECT]
    _gcloud("compute", "ssh", INSTANCE, *iap, "--command", f"mkdir -p {REMOTE_DIR} && chmod 700 {REMOTE_DIR}")
    _gcloud(
        "compute",
        "scp",
        *iap,
        str(td / "body.json"),
        str(td / "token"),
        f"{INSTANCE}:{REMOTE_DIR}/",
    )
    remote = (
        "set -e; "
        f"TOKEN=$(cat {REMOTE_DIR}/token); "
        f"CODE=$(curl -sk -o {REMOTE_DIR}/out.json -w '%{{http_code}}' "
        "-X POST https://127.0.0.1:4443/api/1/vehicles/fleet_telemetry_config "
        '-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" '
        f"--data-binary @{REMOTE_DIR}/body.json); "
        'echo http=$CODE; '
        f"python3 -c \"import json; p=json.load(open('{REMOTE_DIR}/out.json')); "
        "r=p.get('response') if isinstance(p, dict) else None; "
        "print('updated_vehicles', (r or p).get('updated_vehicles') if isinstance(r or p, dict) else None)\"; "
        f"rm -f {REMOTE_DIR}/token {REMOTE_DIR}/body.json {REMOTE_DIR}/out.json"
    )
    out = _gcloud_out("compute", "ssh", INSTANCE, *iap, "--command", remote)
    print(out.strip())
    if "http=200" not in out:
        print("ok=false reason=proxy_http", file=sys.stderr)
        return 1
    print("ok=true via=gce_tesla_http_proxy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
