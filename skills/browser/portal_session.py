#!/usr/bin/env python3
"""Portal automation session — reusable login→MFA→download→upload pipeline.

NOT a standalone runner. This module provides helper functions that CHITRA
(the AI agent) calls while driving Playwright MCP tools. The AI handles
the actual page navigation and element interaction; this module handles
the surrounding orchestration:

  - Credential retrieval from Keychain
  - OTP request/delivery via Slack
  - Download file management (naming, dedup, staging)
  - Google Drive upload with folder routing
  - Registry update after successful download

Typical flow (CHITRA as orchestrator):
    1. session = PortalSession("schwab")
    2. creds = session.get_credentials()         # reads Keychain
    3. CHITRA navigates to login page, fills creds using Playwright MCP
    4. If MFA: otp = session.request_otp(phone_hint="+1-XXX-XXX-XXXX")
    5. CHITRA enters OTP, navigates to tax docs page
    6. CHITRA clicks download → file lands in downloads dir
    7. session.stage_download("1099-composite-2025.pdf", doc_type="1099")
    8. session.upload_all()                       # uploads to Drive, updates registry
"""

import json
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import load_config, refresh_access_token, project_dir

DOWNLOADS_DIR = pathlib.Path(project_dir()) / "extracted" / "downloads"
REGISTRY_PATH = pathlib.Path(project_dir()) / "agents" / "chitra" / "knowledge-base" / "document-registry.json"


class PortalSession:
    """Manages a single portal automation session."""

    def __init__(self, portal_name, keychain_service=None):
        """
        Args:
            portal_name: Human-readable portal name (e.g. "Schwab", "E*Trade")
            keychain_service: macOS Keychain service name (e.g. "jarvis-schwab").
                             Defaults to "jarvis-{portal_name.lower()}"
        """
        self.portal_name = portal_name
        self.keychain_service = keychain_service or f"jarvis-{portal_name.lower().replace(' ', '-').replace('*', '')}"
        self.staged_files = []
        self._config = load_config()
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

    def get_credentials(self):
        """Retrieve username and password from macOS Keychain.

        Returns:
            dict with 'username' and 'password'
        """
        cmd = f"security find-generic-password -s {self.keychain_service} -g 2>&1"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        output = result.stdout + result.stderr

        username = None
        password = None
        for line in output.split("\n"):
            if '"acct"' in line:
                username = line.split('"')[-2] if line.count('"') >= 4 else None
            if "password:" in line:
                pw_part = line.split("password: ", 1)[-1].strip()
                if pw_part.startswith('"') and pw_part.endswith('"'):
                    password = pw_part[1:-1]
                elif pw_part.startswith("0x"):
                    hex_str = pw_part.split("  ")[0].replace("0x", "")
                    password = bytes.fromhex(hex_str).decode("utf-8", errors="replace")

        if not username or not password:
            raise RuntimeError(
                f"Could not retrieve credentials for {self.keychain_service}. "
                f"Store them: python credentials/store_credential.py {self.keychain_service} <user> <pass>"
            )
        return {"username": username, "password": password}

    def request_otp(self, phone_hint=None, timeout_seconds=300):
        """Request OTP from user via Slack DM. Returns the code or None.

        Args:
            phone_hint: Masked phone number shown to user (e.g. "+1-XXX-XXX-XXXX")
            timeout_seconds: How long to wait for a reply
        """
        from skills.slack.adapter import request_otp as slack_request_otp

        user_id = self._config.get("slack", {}).get("primary_user_id")
        if not user_id:
            print(f"[portal] No slack.primary_user_id in config — cannot request OTP")
            return None

        return slack_request_otp(
            user_id=user_id,
            portal_name=self.portal_name,
            timeout_seconds=timeout_seconds,
            phone_hint=phone_hint,
        )

    def download_path(self, filename):
        """Get the full path for a download file.

        Args:
            filename: Desired filename (e.g. "schwab-1099-composite-2025.pdf")

        Returns:
            pathlib.Path to the download location
        """
        return DOWNLOADS_DIR / filename

    def stage_download(self, local_path, doc_type, issuer=None, account_hint=None, tax_year=None):
        """Register a downloaded file for batch upload to Drive.

        Args:
            local_path: Path to the downloaded file (str or Path)
            doc_type: Document type (e.g. "1099", "1098", "W-2")
            issuer: Issuer name (defaults to portal_name)
            account_hint: Account identifier (e.g. "...965")
            tax_year: Tax year (defaults to config profile.tax_year)
        """
        local_path = pathlib.Path(local_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Downloaded file not found: {local_path}")

        self.staged_files.append({
            "local_path": str(local_path),
            "doc_type": doc_type,
            "issuer": issuer or self.portal_name,
            "account_hint": account_hint,
            "tax_year": tax_year or self._config.get("profile", {}).get("tax_year", 2025),
            "size_bytes": local_path.stat().st_size,
            "downloaded_at": time.time(),
        })
        print(f"[portal] Staged: {local_path.name} ({doc_type}, {local_path.stat().st_size:,} bytes)")

    def upload_all(self, drive_folder_id=None):
        """Upload all staged files to Google Drive and update the registry.

        Args:
            drive_folder_id: Target Drive folder. If None, uses the portal's
                            configured subfolder from config.yaml.

        Returns:
            List of upload results with Drive file IDs
        """
        if not self.staged_files:
            print("[portal] Nothing staged for upload")
            return []

        from skills.google_drive.upload import upload_file

        token = refresh_access_token()
        folder_id = drive_folder_id or self._resolve_drive_folder()
        results = []

        for staged in self.staged_files:
            local_path = staged["local_path"]
            drive_name = self._drive_filename(staged)

            try:
                result = upload_file(token, local_path, folder_id, drive_name)
                staged["drive_id"] = result.get("id")
                staged["drive_name"] = drive_name
                staged["uploaded"] = True
                results.append(staged)
                print(f"[portal] Uploaded to Drive: {drive_name} [{result.get('id')}]")
            except Exception as e:
                staged["uploaded"] = False
                staged["error"] = str(e)
                results.append(staged)
                print(f"[portal] Upload failed for {drive_name}: {e}")

        self._update_registry(results)
        self.staged_files = []
        return results

    def notify_status(self, message):
        """Send a status update to the user via Slack."""
        try:
            from skills.slack.adapter import send_message
            dm_channel = self._config.get("slack", {}).get("dm_channel")
            if dm_channel:
                send_message(dm_channel, message)
        except Exception as e:
            print(f"[portal] Slack notification failed: {e}")

    def _resolve_drive_folder(self):
        """Find the right Drive folder for this portal's documents."""
        subfolder_ids = self._config.get("google_drive", {}).get("subfolder_ids", {})
        portal_key = self.portal_name.lower().replace(" ", "-").replace("*", "")
        folder_id = subfolder_ids.get(portal_key)
        if folder_id:
            return folder_id
        return self._config.get("google_drive", {}).get("taxes_year_id")

    def _drive_filename(self, staged):
        """Generate a consistent Drive filename."""
        parts = []
        if staged.get("issuer"):
            parts.append(staged["issuer"].replace(" ", "-").replace("/", "-"))
        parts.append(staged["doc_type"])
        if staged.get("account_hint"):
            parts.append(staged["account_hint"].replace("...", ""))
        parts.append(str(staged.get("tax_year", 2025)))

        base = "_".join(parts)
        ext = pathlib.Path(staged["local_path"]).suffix or ".pdf"
        return f"{base}{ext}"

    def _update_registry(self, results):
        """Update document-registry.json with successful uploads."""
        if not REGISTRY_PATH.exists():
            return

        try:
            registry = json.loads(REGISTRY_PATH.read_text())
        except (json.JSONDecodeError, IOError):
            return

        docs = registry.get("documents", [])
        updated = False

        for result in results:
            if not result.get("uploaded"):
                continue

            for doc in docs:
                if (self._normalize(doc.get("docType", "")) == self._normalize(result["doc_type"])
                        and self._normalize(doc.get("issuer", "")) == self._normalize(result["issuer"])):
                    doc["status"] = "received"
                    doc["driveFileId"] = result.get("drive_id")
                    doc["downloadedAt"] = time.strftime("%Y-%m-%d")
                    doc["source"] = f"auto:{self.portal_name}"
                    updated = True
                    break

        if updated:
            REGISTRY_PATH.write_text(json.dumps(registry, indent=2))
            print(f"[portal] Registry updated: {REGISTRY_PATH}")

    @staticmethod
    def _normalize(s):
        return s.lower().strip().replace("-", "").replace(" ", "")


def list_keychain_portals():
    """List all jarvis-* portal credentials in Keychain.

    Uses targeted lookups for known portals (from portals.yaml.template)
    rather than parsing the noisy dump-keychain output.
    """
    known_services = [
        "jarvis-schwab", "jarvis-etrade", "jarvis-robinhood",
        "jarvis-wells-fargo", "jarvis-chase", "jarvis-fidelity",
        "jarvis-hsa-bank", "jarvis-homebase", "jarvis-obie",
        "jarvis-nationwide", "jarvis-allianz", "jarvis-ziprent",
        "jarvis-yardi",
    ]

    portals = []
    for svc in known_services:
        cmd = f"security find-generic-password -s {svc} 2>&1"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        combined = result.stdout + result.stderr
        if "could not be found" in combined or result.returncode != 0:
            continue
        username = None
        for line in combined.split("\n"):
            if '"acct"' in line:
                parts = line.split('"')
                if len(parts) >= 4:
                    username = parts[-2]
                    break
        if username:
            portals.append({
                "service": svc,
                "portal": svc.replace("jarvis-", ""),
                "username": username,
            })

    return portals


if __name__ == "__main__":
    print("Portal credentials in Keychain:\n")
    portals = list_keychain_portals()
    if portals:
        for p in portals:
            print(f"  {p['service']:25s}  user: {p['username']}")
    else:
        print("  No jarvis-* credentials found")
    print(f"\nDownloads dir: {DOWNLOADS_DIR}")
    print(f"Registry: {REGISTRY_PATH}")
