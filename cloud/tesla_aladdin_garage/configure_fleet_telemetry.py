"""POST fleet_telemetry_config through tesla-http-proxy. Never prints tokens.

Run on GCE tesla-fleet-telemetry (proxy on https://127.0.0.1:4443) or on a
laptop with `gcloud compute start-iap-tunnel … 4443`.
"""

from __future__ import annotations

import json
import os
import sys

from skills.tesla_fleet.client import TeslaFleetClient


def main() -> int:
    proxy = os.environ.get("TESLA_COMMAND_PROXY_URL", "https://127.0.0.1:4443").strip()
    host = os.environ.get("TESLA_TELEMETRY_HOST", "35.239.192.226.sslip.io").strip()
    port = int(os.environ.get("TESLA_TELEMETRY_PORT", "8443") or 8443)
    vin = os.environ.get("TESLA_VIN", "7SAYGAEE2TF605512").strip()
    ca = os.environ.get("TESLA_TELEMETRY_CA", "").strip()
    tesla = TeslaFleetClient.from_env(command_proxy_url=proxy)
    if tesla.needs_user_auth():
        print("ok=false reason=needs_reauth", file=sys.stderr)
        return 2
    try:
        resp = tesla.put_fleet_telemetry_config(
            vins=[vin],
            hostname=host,
            port=port,
            ca=ca,
        )
    except Exception as e:
        status = getattr(e, "status", None)
        print(f"ok=false reason=telemetry_config status={status} err_type={type(e).__name__}", file=sys.stderr)
        return 1
    updated = None
    if isinstance(resp, dict):
        inner = resp.get("response") if isinstance(resp.get("response"), dict) else resp
        if isinstance(inner, dict):
            updated = inner.get("updated_vehicles")
    print(json.dumps({"ok": True, "via": "command_proxy", "updated_vehicles": updated}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
