#!/usr/bin/env python3
"""CHITRA Portal Collector — orchestrates browser automation for tax document retrieval.

This script reads portal playbook YAML files and credentials to drive
Playwright MCP for automated document downloads. It handles:
  - Loading portal configurations and credentials
  - Sequencing login → OTP → navigation → download steps
  - Uploading downloaded files to Google Drive
  - Updating the document registry with results

Prerequisites:
  - Playwright MCP configured in .cursor/mcp.json
  - Slack MCP configured for OTP notifications
  - credentials/portals.yaml with portal credentials (gitignored)
  - macOS Keychain entries for each portal password

Usage:
  This script is designed to be called by CHITRA (the AI agent) rather than
  run standalone. CHITRA reads the playbooks and uses Playwright MCP tools
  directly. This orchestrator provides helper functions for that workflow.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import (
    project_dir,
    kb_path,
    refresh_access_token,
    get_drive_id,
)

PLAYBOOKS_DIR = kb_path("portal-playbooks")
CREDS_PATH = os.path.join(project_dir(), "credentials", "portals.yaml")
DOWNLOADS_DIR = os.path.join(project_dir(), "extracted", "downloads")


def _load_yaml_simple(path):
    """Minimal YAML parser (same as config_loader — avoids PyYAML dep)."""
    result = {}
    current_section = None
    current_list_key = None

    with open(path) as f:
        for line in f:
            stripped = line.rstrip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            if indent == 0 and stripped.endswith(":"):
                current_section = stripped[:-1]
                result[current_section] = {}
                current_list_key = None
                continue
            if indent == 0 and ": " in stripped:
                key, val = stripped.split(": ", 1)
                result[key] = val.strip().strip('"').strip("'")
                current_section = None
                continue
            if current_section is not None:
                if stripped.lstrip().startswith("- "):
                    item = stripped.lstrip()[2:].strip().strip('"').strip("'")
                    if current_list_key:
                        if current_list_key not in result[current_section]:
                            result[current_section][current_list_key] = []
                        result[current_section][current_list_key].append(item)
                    continue
                if ": " in stripped.lstrip():
                    key, val = stripped.lstrip().split(": ", 1)
                    result[current_section][key] = val.strip().strip('"').strip("'")
                elif stripped.lstrip().endswith(":"):
                    current_list_key = stripped.lstrip()[:-1]
    return result


def load_playbook(portal_key):
    """Load a portal playbook YAML file."""
    path = os.path.join(PLAYBOOKS_DIR, f"{portal_key}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No playbook found for portal: {portal_key}")
    return _load_yaml_simple(path)


def load_credentials(portal_key):
    """Load credentials for a specific portal from portals.yaml."""
    if not os.path.exists(CREDS_PATH):
        raise FileNotFoundError(
            f"credentials/portals.yaml not found. Copy from credentials/portals.template.yaml."
        )
    creds = _load_yaml_simple(CREDS_PATH)
    portals = creds.get("portals", {})
    if portal_key not in portals:
        raise KeyError(f"No credentials found for portal: {portal_key}")
    return portals[portal_key]


def get_password(password_cmd):
    """Execute the Keychain command to retrieve a password."""
    try:
        result = subprocess.run(
            password_cmd, shell=True, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            raise RuntimeError(f"Keychain command failed: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Keychain command timed out")


def list_available_portals():
    """List all portal playbooks that are available and automated."""
    if not os.path.exists(PLAYBOOKS_DIR):
        return []

    portals = []
    for fname in sorted(os.listdir(PLAYBOOKS_DIR)):
        if fname.endswith(".yaml"):
            key = fname[:-5]
            try:
                pb = load_playbook(key)
                automated = pb.get("automated", "true").lower() != "false"
                portals.append({
                    "key": key,
                    "name": pb.get("name", key),
                    "automated": automated,
                    "login_required": pb.get("login_required", "true").lower() != "false",
                })
            except Exception:
                pass
    return portals


def ensure_downloads_dir():
    """Create the downloads directory if it doesn't exist."""
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    return DOWNLOADS_DIR


def upload_downloaded_file(local_path, drive_folder_id, drive_name=None):
    """Upload a downloaded file to Google Drive."""
    from skills.google_drive.upload import upload_file

    token = refresh_access_token()
    return upload_file(token, local_path, drive_folder_id, drive_name)


def main():
    """List available portals and their automation status."""
    portals = list_available_portals()
    if not portals:
        print("No portal playbooks found.")
        print(f"Expected location: {PLAYBOOKS_DIR}/")
        return

    print(f"Available portal playbooks ({len(portals)}):\n")
    for p in portals:
        status = "AUTOMATED" if p["automated"] else "MANUAL (CPA access)"
        login = "login required" if p["login_required"] else "public"
        print(f"  {p['key']:20s}  {p['name']:25s}  [{status}]  ({login})")

    print(f"\nPlaybooks directory: {PLAYBOOKS_DIR}/")
    print(f"Downloads directory: {DOWNLOADS_DIR}/")


if __name__ == "__main__":
    main()
