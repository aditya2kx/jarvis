#!/usr/bin/env python3
"""Shared configuration loader for all Jarvis agents and skills.

Reads config.yaml (gitignored, user-specific) and provides helpers for
Google API authentication and resource IDs. Falls back to config.template.yaml
for structure reference if config.yaml is missing.
"""

import json
import os
import urllib.parse
import urllib.request

import yaml

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
_CONFIG_PATH = os.path.join(_PROJECT_DIR, "config.yaml")
_TEMPLATE_PATH = os.path.join(_PROJECT_DIR, "config.template.yaml")

_config = None


def load_config():
    """Load and cache configuration."""
    global _config
    if _config is not None:
        return _config

    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH) as f:
            _config = yaml.safe_load(f) or {}
    elif os.path.exists(_TEMPLATE_PATH):
        raise FileNotFoundError(
            f"config.yaml not found. Copy config.template.yaml to config.yaml and fill in your values.\n"
            f"  cp {_TEMPLATE_PATH} {_CONFIG_PATH}"
        )
    else:
        raise FileNotFoundError("Neither config.yaml nor config.template.yaml found.")

    return _config


def get_auth_paths(account=None):
    """Return (credentials_path, env_path) from config.

    If account is given, looks up the named account under the 'accounts'
    section.  Otherwise falls back to the legacy 'auth' section for
    backward compatibility with CHITRA and other existing callers.
    """
    cfg = load_config()

    if account is not None:
        accounts = cfg.get("accounts", {})
        if account not in accounts:
            raise KeyError(
                f"Account '{account}' not found in config.yaml accounts section. "
                f"Available: {list(accounts.keys())}"
            )
        acct = accounts[account]
        creds = os.path.expanduser(acct.get("credentials_path", ""))
        env = os.path.expanduser(acct.get("env_path", ""))
        return creds, env

    auth = cfg.get("auth", {})
    creds = os.path.expanduser(auth.get("credentials_path", ""))
    env = os.path.expanduser(auth.get("env_path", ""))
    return creds, env


def get_account_config(account):
    """Return the full config dict for a named account."""
    cfg = load_config()
    accounts = cfg.get("accounts", {})
    if account not in accounts:
        raise KeyError(
            f"Account '{account}' not found in config.yaml accounts section. "
            f"Available: {list(accounts.keys())}"
        )
    return accounts[account]


def get_sheet_id(name):
    """Get a Google Sheets spreadsheet ID by config key name."""
    cfg = load_config()
    return cfg.get("google_sheets", {}).get(name, "")


def get_drive_id(name):
    """Get a Google Drive folder ID by config key name."""
    cfg = load_config()
    drive = cfg.get("google_drive", {})
    if name in drive:
        return drive[name]
    return drive.get("subfolder_ids", {}).get(name, "")


def get_profile():
    """Get profile section from config."""
    cfg = load_config()
    return cfg.get("profile", {})


def project_dir():
    """Return the project root directory."""
    return _PROJECT_DIR


def kb_path(*parts):
    """Build a path relative to the knowledge-base directory."""
    cfg = load_config()
    kb = cfg.get("paths", {}).get("knowledge_base", "knowledge-base")
    return os.path.join(_PROJECT_DIR, kb, *parts)


def load_env(path):
    """Parse a .env file into a dict."""
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def refresh_access_token(account=None):
    """Refresh and return a Google API access token.

    Args:
        account: Named account from the 'accounts' config section
                 (e.g. 'personal', 'palmetto').  None falls back to
                 the legacy 'auth' section for backward compatibility.
    """
    creds_path, env_path = get_auth_paths(account)
    with open(creds_path) as f:
        creds = json.load(f)
    env = load_env(env_path)
    data = urllib.parse.urlencode({
        "client_id": env["CLIENT_ID"],
        "client_secret": env["CLIENT_SECRET"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]
