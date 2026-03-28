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

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
_CONFIG_PATH = os.path.join(_PROJECT_DIR, "config.yaml")
_TEMPLATE_PATH = os.path.join(_PROJECT_DIR, "config.template.yaml")

_config = None


def _load_yaml_simple(path):
    """Minimal YAML-subset parser (avoids PyYAML dependency).
    Handles flat and one-level nested key: value pairs, plus lists with '- ' prefix."""
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
                result[key] = _parse_value(val)
                current_section = None
                current_list_key = None
                continue

            if current_section is not None:
                if stripped.lstrip().startswith("- "):
                    item = stripped.lstrip()[2:].strip()
                    if current_list_key:
                        result[current_section][current_list_key].append(_parse_value(item))
                    continue

                if ": " in stripped.lstrip():
                    key, val = stripped.lstrip().split(": ", 1)
                    parsed = _parse_value(val)
                    result[current_section][key] = parsed
                    current_list_key = None
                elif stripped.lstrip().endswith(":"):
                    key = stripped.lstrip()[:-1]
                    result[current_section][key] = []
                    current_list_key = key

    return result


def _parse_value(val):
    val = val.strip()
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    if val.startswith("'") and val.endswith("'"):
        return val[1:-1]
    if val.isdigit():
        return int(val)
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    return val


def load_config():
    """Load and cache configuration."""
    global _config
    if _config is not None:
        return _config

    if os.path.exists(_CONFIG_PATH):
        _config = _load_yaml_simple(_CONFIG_PATH)
    elif os.path.exists(_TEMPLATE_PATH):
        raise FileNotFoundError(
            f"config.yaml not found. Copy config.template.yaml to config.yaml and fill in your values.\n"
            f"  cp {_TEMPLATE_PATH} {_CONFIG_PATH}"
        )
    else:
        raise FileNotFoundError("Neither config.yaml nor config.template.yaml found.")

    return _config


def get_auth_paths():
    """Return (credentials_path, env_path) from config."""
    cfg = load_config()
    auth = cfg.get("auth", {})
    creds = os.path.expanduser(auth.get("credentials_path", ""))
    env = os.path.expanduser(auth.get("env_path", ""))
    return creds, env


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


def refresh_access_token():
    """Refresh and return a Google API access token using config credentials."""
    creds_path, env_path = get_auth_paths()
    creds = json.load(open(creds_path))
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
