#!/usr/bin/env python3
"""Square app provisioning — post-Playwright finalizer.

After the AI drives `user-playwright` through Square Developer Dashboard and
captures `access_token` (EAA...), `application_id` (sq0idp-...), and
`location_id` (L...), call `register_store()` to:

    1. Store the PAT in Keychain via skills/credentials/.add_keychain()
       (service jarvis-square-<store>, account SQUARE_ACCESS_TOKEN_<STORE>)
    2. Write (or update) the BHAGA store-profile JSON at
       agents/bhaga/knowledge-base/store-profiles/<store>.json
    3. Send a confirmation DM as BHAGA on Slack

Idempotent: re-running with the same store + new tokens overwrites Keychain
entry and the store-profile JSON without duplicating; safe to retry after
partial failure.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import load_config, project_dir
from skills.credentials import registry as cred_registry


def _service(store: str) -> str:
    return f"jarvis-square-{store.lower()}"


def _account(store: str) -> str:
    return f"SQUARE_ACCESS_TOKEN_{store.upper()}"


def _profile_path(store: str) -> pathlib.Path:
    return (
        pathlib.Path(project_dir())
        / "agents" / "bhaga" / "knowledge-base" / "store-profiles"
        / f"{store.lower()}.json"
    )


def store_token(store: str, access_token: str) -> dict:
    """Store the Square PAT in Keychain via skills/credentials."""
    if not access_token or not access_token.startswith("EAA"):
        raise ValueError(
            f"access_token must start with 'EAA' (Square production PAT), got: {access_token!r}"
        )
    return cred_registry.add_keychain(
        name=f"square_{store.lower()}",
        service=_service(store),
        account=_account(store),
        password=access_token,
        portal=f"square-{store.lower()}",
        notes=(
            f"Square Personal Access Token for the {store.capitalize()} store. "
            f"Full account access (PAT model). Used by skills/square_tips/ + "
            f"any other Square-reading skill BHAGA composes."
        ),
    )


def write_store_profile(
    store: str,
    application_id: str,
    location_id: str,
    timezone: str,
    extras: Optional[dict] = None,
) -> pathlib.Path:
    """Create or update the store-profile JSON for this store.

    Schema is intentionally extensible — `skills/square_tips/`, `skills/adp_run_automation/`,
    `skills/tip_ledger_writer/` all read from here. Future fields (sheet_id,
    pay period schedule, ADP company code, etc.) get added in place by the
    relevant register/configure step.
    """
    path = _profile_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            profile = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            profile = {}
    else:
        profile = {}

    profile.update({
        "store": store.lower(),
        "timezone": timezone,
        "square": {
            "application_id": application_id,
            "location_id": location_id,
            "access_token_keychain": {
                "service": _service(store),
                "account": _account(store),
                "type": "personal_access_token",
            },
            "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
    })
    if extras:
        profile.update(extras)

    path.write_text(json.dumps(profile, indent=2, sort_keys=True))
    return path


def send_first_dm(store: str, application_id: str, location_id: str) -> dict:
    """Send a confirmation DM via the BHAGA Slack agent."""
    from skills.slack.adapter import set_agent, send_progress
    set_agent("bhaga")
    text = (
        f":square_white: *Square credentials provisioned — {store.upper()}*\n\n"
        f"  • app_id: `{application_id}`\n"
        f"  • location_id: `{location_id}`\n"
        f"  • PAT stored in Keychain at service `{_service(store)}` / "
        f"account `{_account(store)}`\n"
        f"  • store profile: `agents/bhaga/knowledge-base/store-profiles/{store.lower()}.json`\n\n"
        f"`skills/square_tips/` can now resolve credentials for this store. "
        f"Ready for M1 (daily card-tip totals into the Austin sheet) on your go."
    )
    return send_progress(text, agent="bhaga") or {}


def register_store(
    store: str,
    access_token: str,
    application_id: str,
    location_id: str,
    timezone: str = "America/Chicago",
    notify: bool = True,
) -> dict:
    """One call → full Square credential wiring for the named store."""
    if not application_id or not application_id.startswith("sq0idp-"):
        raise ValueError(
            f"application_id should start with 'sq0idp-', got: {application_id!r}"
        )
    if not location_id or not location_id.startswith("L"):
        raise ValueError(
            f"location_id should start with 'L', got: {location_id!r}"
        )

    keychain = store_token(store, access_token)
    profile_path = write_store_profile(
        store=store,
        application_id=application_id,
        location_id=location_id,
        timezone=timezone,
    )

    summary = {
        "store": store.lower(),
        "application_id": application_id,
        "location_id": location_id,
        "timezone": timezone,
        "keychain": keychain,
        "profile_path": str(profile_path),
    }

    if notify:
        try:
            dm = send_first_dm(store, application_id, location_id)
            summary["confirmation_dm_ts"] = dm.get("ts")
        except Exception as e:
            summary["confirmation_dm_error"] = str(e)

    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Square store credential register")
    parser.add_argument("--store", required=True, help="Logical store name (austin, houston, ...)")
    parser.add_argument("--access-token", required=True, help="EAA... (production PAT)")
    parser.add_argument("--application-id", required=True, help="sq0idp-...")
    parser.add_argument("--location-id", required=True, help="L... (13-char Square location id)")
    parser.add_argument("--timezone", default="America/Chicago", help="Store local timezone")
    parser.add_argument("--no-notify", action="store_true", help="Skip the BHAGA Slack DM")
    args = parser.parse_args()

    summary = register_store(
        store=args.store,
        access_token=args.access_token,
        application_id=args.application_id,
        location_id=args.location_id,
        timezone=args.timezone,
        notify=not args.no_notify,
    )
    print(json.dumps(summary, indent=2, default=str))
