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
_PRODUCTION_SHEET_IDS: frozenset | None = None


def _load_production_sheet_ids() -> frozenset:
    """Scan all store profiles and collect every production spreadsheet_id."""
    global _PRODUCTION_SHEET_IDS
    if _PRODUCTION_SHEET_IDS is not None:
        return _PRODUCTION_SHEET_IDS
    store_profiles_dir = os.path.join(
        _PROJECT_DIR, "agents", "bhaga", "knowledge-base", "store-profiles",
    )
    ids: set = set()
    if os.path.isdir(store_profiles_dir):
        for filename in os.listdir(store_profiles_dir):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(store_profiles_dir, filename)
            try:
                with open(path) as f:
                    profile = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            for sheet_info in profile.get("google_sheets", {}).values():
                if isinstance(sheet_info, dict) and "spreadsheet_id" in sheet_info:
                    ids.add(sheet_info["spreadsheet_id"])
    _PRODUCTION_SHEET_IDS = frozenset(ids)
    return _PRODUCTION_SHEET_IDS


def _assert_not_production_sheet(spreadsheet_id: str) -> None:
    """Hard guard: when running in staging mode, block any access to production sheets."""
    if os.environ.get("BHAGA_SHEET_MODE", "").lower() != "staging":
        return
    prod_ids = _load_production_sheet_ids()
    if spreadsheet_id in prod_ids:
        raise RuntimeError(
            f"BLOCKED: Cloud flow attempted to access production sheet {spreadsheet_id}. "
            f"BHAGA_SHEET_MODE=staging requires exclusive use of staging sheets. "
            f"Production sheet IDs are loaded from store-profiles/*.json google_sheets section."
        )


def load_config():
    """Load and cache configuration.

    In cloud environments (BHAGA_SECRETS_BACKEND=gcp), returns an empty dict
    if config.yaml is absent — cloud deploys don't carry the local config file.
    """
    global _config
    if _config is not None:
        return _config

    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH) as f:
            _config = yaml.safe_load(f) or {}
    elif os.environ.get("BHAGA_SECRETS_BACKEND", "").lower() == "gcp":
        _config = {}
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


def resolve_sheet_id(profile_key: str, profile: dict) -> str:
    """Resolve a Google Sheets spreadsheet ID, respecting BHAGA_SHEET_MODE.

    When BHAGA_SHEET_MODE=staging, looks up env var overrides first:
      BHAGA_STAGING_{KEY}_SID  (e.g. BHAGA_STAGING_BHAGA_MODEL_SID)
    Falls back to profile["google_sheets_staging"][profile_key]["spreadsheet_id"]
    if present, otherwise returns the prod ID from the profile.

    profile_key: one of "bhaga_model", "bhaga_adp_raw", "bhaga_square_raw",
                 "bhaga_review_raw"
    """
    mode = os.environ.get("BHAGA_SHEET_MODE", "prod").lower()
    if mode == "staging":
        env_key = f"BHAGA_STAGING_{profile_key.upper()}_SID"
        env_val = os.environ.get(env_key)
        if env_val:
            _assert_not_production_sheet(env_val)
            return env_val
        staging = profile.get("google_sheets_staging", {})
        if profile_key in staging:
            sid = staging[profile_key]["spreadsheet_id"]
            _assert_not_production_sheet(sid)
            return sid
    sid = profile["google_sheets"][profile_key]["spreadsheet_id"]
    _assert_not_production_sheet(sid)
    return sid


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


# ── Forecast config keys ──────────────────────────────────────────

_FORECAST_DEFAULTS = {
    "forecast_target_labor_pct": 0.25,
    # Hourly (part-time-only) labor% target for the forecast tab's
    # hourly_staffing_flag — hourly_cost / net_sales, EXCLUDING Lindsay's
    # full-time cost. Seeded on every future forecast row but editable per row.
    "forecast_target_hourly_labor_pct": 0.20,
    "forecast_fulltime_weekly_hours": 40,
    # Flat, configurable staffing-solver target — NOT derived from observed KDS.
    # 420s = 7 min/item (bumped from the old 300s/5 min default). Also editable
    # per forecast row via the labor_daily_forecast `target_time_per_item_sec`
    # input column, and reused as the goal for the operational kds_pct_items_over_goal metric.
    "forecast_target_completion_time_per_item_sec": 420,
    # Trend-aware robust outlier detection (replaces the old flat 25% rule).
    "forecast_outlier_window_weeks": 8,
    "forecast_outlier_z_threshold": 2.5,
}


def get_forecast_config(config_rows: list[list] | None = None) -> dict:
    """Read forecast configuration from the config tab rows.

    Args:
        config_rows: The raw [[key, value, notes], ...] from the model sheet's
            config tab. If None, returns defaults.

    Returns dict with keys:
        forecast_target_labor_pct (float, default 0.25)
        forecast_target_hourly_labor_pct (float, default 0.20) — hourly
            (part-time-only) labor% target; drives hourly_staffing_flag.
        forecast_fulltime_weekly_hours (float, default 40)
        forecast_target_completion_time_per_item_sec (float, default 420 = 7 min/item)
        forecast_outlier_window_weeks (float, default 8) — trailing window of
            residuals the robust-z dispersion (median/MAD) is computed over.
        forecast_outlier_z_threshold (float, default 2.5) — |robust_z| beyond
            this flags an outlier; z below the negative of this (with actual <
            expected) auto-excludes an anomalous LOW (stock-out / early-close).
    """
    result = dict(_FORECAST_DEFAULTS)
    if config_rows is None:
        return result
    for row in config_rows:
        if not row or len(row) < 2:
            continue
        key = str(row[0]).strip()
        if key in _FORECAST_DEFAULTS:
            try:
                result[key] = float(row[1])
            except (ValueError, TypeError):
                pass
    return result


def refresh_access_token(account=None):
    """Refresh and return a Google API access token.

    Args:
        account: Named account from the 'accounts' config section
                 (e.g. 'personal', 'palmetto').  None falls back to
                 the legacy 'auth' section for backward compatibility.

    In cloud environments (BHAGA_SECRETS_BACKEND=gcp), uses Application
    Default Credentials (service account) instead of local OAuth files.
    """
    if os.environ.get("BHAGA_SECRETS_BACKEND", "").lower() == "gcp":
        import google.auth
        import google.auth.transport.requests

        SCOPES = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credentials, _ = google.auth.default(scopes=SCOPES)
        credentials.refresh(google.auth.transport.requests.Request())
        return credentials.token

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
