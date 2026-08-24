"""CLI: verify-key, register-partner, vehicles, doors, tick, serve."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from cloud.tesla_aladdin_garage.worker import GarageWorker, WorkerConfig
from skills.aladdin_connect.client import AladdinConnectClient
from skills.tesla_fleet.client import TeslaFleetClient


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="tesla-aladdin-garage")
    p.add_argument(
        "command",
        choices=["verify-key", "register-partner", "vehicles", "doors", "tick", "serve"],
    )
    args = p.parse_args(argv)
    tesla = TeslaFleetClient.from_env()
    if args.command == "verify-key":
        pem = tesla.fetch_hosted_public_key()
        print(json.dumps({"hosted_pem_prefix": pem[:40], "api": tesla.verify_partner_key()}, default=str))
        return 0
    if args.command == "register-partner":
        print(json.dumps(tesla.register_partner(), default=str))
        return 0
    if args.command == "vehicles":
        print(json.dumps(tesla.list_vehicles(), default=str, indent=2))
        return 0
    if args.command == "doors":
        print(json.dumps(AladdinConnectClient.from_env().list_doors(), default=str, indent=2))
        return 0
    if args.command == "tick":
        print(GarageWorker(WorkerConfig.from_env(), tesla, AladdinConnectClient.from_env()).tick())
        return 0
    if args.command == "serve":
        from cloud.tesla_aladdin_garage.app import app

        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
