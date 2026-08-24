#!/usr/bin/env python3
"""Classify this process's GCP identity. No network writes. Never prints secrets.

    python3 scripts/gcp_access_probe.py

Canonical recipes: docs/contributing/gcp-access.md
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


RECIPES = {
    "github_actions_wif": (
        "GitHub Actions + WIF. Use workflow gcloud / google-github-actions/auth. "
        "Do not put secret values in workflow_dispatch inputs."
    ),
    "adc_ready": (
        "ADC is available. Use python3 scripts/secret_manager_put.py or "
        "python3 -m skills.credentials.registry hydrate-all. gcloud CLI is optional."
    ),
    "cursor_cloud_no_adc": (
        "Cursor Cloud Agent with no GCP identity. Expected. Do not mint a JSON key. "
        "Put Secret Manager versions from a laptop/Cloud Shell with ADC "
        "(docs/contributing/gcp-access.md recipe D)."
    ),
    "gcloud_cli_no_adc": (
        "gcloud is on PATH but ADC is missing. Run: "
        "gcloud auth application-default login && gcloud config set project jarvis-bhaga-prod"
    ),
    "no_gcp_identity": (
        "No ADC, no GHA WIF, no gcloud. Same as recipe D unless this is a laptop — "
        "then install gcloud or use Cloud Shell (docs/contributing/gcp-access.md)."
    ),
}


def _cursor_cloud_agent(env: dict[str, str]) -> bool:
    if env.get("CURSOR_AGENT"):
        return True
    if env.get("CURSOR_CONVERSATION_ID") and Path("/opt/cursor").is_dir():
        return True
    return False


def probe_adc() -> tuple[bool, str]:
    """Return (ok, project_or_error_type). Does not print credentials."""
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError
    except ImportError:
        return False, "google_auth_not_installed"
    try:
        _creds, project = google.auth.default()
    except DefaultCredentialsError:
        return False, "DefaultCredentialsError"
    except Exception as exc:  # noqa: BLE001 — probe must never crash the CLI
        return False, type(exc).__name__
    return True, project or "(no project)"


def classify(
    *,
    env: dict[str, str] | None = None,
    gcloud_on_path: bool | None = None,
    adc_ok: bool | None = None,
) -> str:
    env = env if env is not None else dict(os.environ)
    if env.get("GITHUB_ACTIONS") == "true":
        return "github_actions_wif"
    if adc_ok is None:
        adc_ok, _ = probe_adc()
    if adc_ok:
        return "adc_ready"
    if gcloud_on_path is None:
        gcloud_on_path = shutil.which("gcloud") is not None
    if _cursor_cloud_agent(env):
        return "cursor_cloud_no_adc"
    if gcloud_on_path:
        return "gcloud_cli_no_adc"
    return "no_gcp_identity"


def format_report(
    surface: str,
    *,
    gcloud_on_path: bool,
    adc_ok: bool,
    adc_detail: str,
) -> str:
    lines = [
        f"surface={surface}",
        f"gcloud_cli={'yes' if gcloud_on_path else 'no'}",
        f"adc={'yes' if adc_ok else 'no'} ({adc_detail})",
        f"recipe={RECIPES[surface]}",
        "doc=docs/contributing/gcp-access.md",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    gcloud_on_path = shutil.which("gcloud") is not None
    adc_ok, adc_detail = probe_adc()
    surface = classify(gcloud_on_path=gcloud_on_path, adc_ok=adc_ok)
    sys.stdout.write(
        format_report(
            surface,
            gcloud_on_path=gcloud_on_path,
            adc_ok=adc_ok,
            adc_detail=adc_detail,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
