#!/usr/bin/env python3
"""skills/store_profile/reader - canonical loader for the per-store knowledge base.

Source of truth (post-2026-05-17 migration): Google Sheets.

  bhaga_model > config       key/value tunables + sheet pointers + exclusions
  bhaga_model > employees    roster + aliases + alt-spellings + notes

Local file `agents/bhaga/knowledge-base/store-profiles/{store}.json` is kept
ONLY as a thin bootstrap pointer (holds `google_sheets.bhaga_model.spreadsheet_id`
+ `google_account_key`) so the loader knows which sheet to query for the rest.
Old callers that still read other sections of that JSON file will continue to
work; new code should go through this module.

The unified profile dict returned by `load_full_profile()` mirrors the OLD
palmetto.json shape closely so callers can migrate field-by-field:

  {
    "store_id":             "palmetto",
    "display_name":         "Palmetto Superfoods (Austin)",
    "legal_entity":         "AK JUICY BOWLS LLC",
    "google_account_key":   "palmetto",
    "timezone":             {"shop_tz": "America/Chicago", ...},
    "google_sheets":        {<bhaga_model spreadsheet ids>},
    "employees": {
      "aliases":            {"raw_name": "canonical_name", ...},
      "roster":             [{"canonical_name": ..., "aliases": [...], "notes": ...}, ...],
      "excluded_from_tip_pool_and_labor_pct": ["Krause, Lindsay"],
      "training_excluded":  {"Flores, Juan": "2026-05-16", ...},
    },
    ...
  }

Sheet reads are cached per-process to avoid hammering the Google API.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import refresh_access_token  # noqa: E402

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
STORE_PROFILES_DIR = PROJECT_ROOT / "agents" / "bhaga" / "knowledge-base" / "store-profiles"


def _bootstrap_pointer(store: str) -> dict:
    """Load the small bootstrap pointer (sheet id + google account key) from disk.

    This is the ONLY part of palmetto.json that's still authoritative. Every
    other piece of the store profile is read from the sheet.
    """
    path = STORE_PROFILES_DIR / f"{store}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Store profile pointer not found at {path}. Each store needs at "
            "least a minimal JSON with google_account_key and "
            "google_sheets.bhaga_model.spreadsheet_id."
        )
    return json.loads(path.read_text())


def _fetch_range(spreadsheet_id: str, range_a1: str, *, account: str) -> list[list[str]]:
    token = refresh_access_token(account)
    rng = urllib.parse.quote(range_a1, safe="!:")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{rng}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("values", [])


@lru_cache(maxsize=4)
def _read_employees_tab(store: str) -> list[dict]:
    """Read bhaga_model > employees and return list of roster dicts."""
    pointer = _bootstrap_pointer(store)
    sid = pointer["google_sheets"]["bhaga_model"]["spreadsheet_id"]
    account = pointer.get("google_account_key", store)
    rows = _fetch_range(sid, "employees!A1:E500", account=account)
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    out = []
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        rec = {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(header)}
        # Parse aliases: split on ';' then trim. Tolerant of legacy comma sep.
        raw = rec.get("aliases", "")
        if ";" in raw:
            aliases = [a.strip() for a in raw.split(";") if a.strip()]
        else:
            aliases = [a.strip() for a in raw.split(",") if a.strip()]
        rec["aliases_list"] = aliases
        out.append(rec)
    return out


@lru_cache(maxsize=4)
def _read_config_tab(store: str) -> dict[str, dict]:
    """Read bhaga_model > config and return {key: {"value": v, "notes": n}}."""
    pointer = _bootstrap_pointer(store)
    sid = pointer["google_sheets"]["bhaga_model"]["spreadsheet_id"]
    account = pointer.get("google_account_key", store)
    rows = _fetch_range(sid, "config!A1:F200", account=account)
    out: dict[str, dict] = {}
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        key = row[0].strip()
        value = row[1].strip() if len(row) > 1 else ""
        notes = row[2].strip() if len(row) > 2 else ""
        out[key] = {"value": value, "notes": notes}
    return out


def load_employee_roster(store: str = "palmetto") -> list[dict]:
    """Return the canonical employee roster from bhaga_model > employees.

    Each record: {canonical_name, aliases, aliases_list, notes}.
    """
    return _read_employees_tab(store)


def load_aliases(store: str = "palmetto") -> dict[str, str]:
    """Return the {raw_name_or_alias: canonical_name} mapping.

    This is the shape callers used to get from palmetto.json's
    employees.aliases dict, so it's a drop-in replacement.

    Includes:
      - The canonical name itself (so canonical -> canonical is in the map)
      - Every alias listed in the `aliases` column on the employees tab
    """
    roster = load_employee_roster(store)
    out: dict[str, str] = {}
    for rec in roster:
        canonical = rec["canonical_name"]
        out[canonical] = canonical
        for alias in rec["aliases_list"]:
            out[alias] = canonical
    return out


def load_exclusions(store: str = "palmetto") -> dict:
    """Return the active exclusion state from bhaga_model > config.

    {
      "permanent": ["Krause, Lindsay", ...],
      "training":  {"Flores, Juan": "2026-05-16", ...},
    }
    """
    cfg = _read_config_tab(store)
    permanent_raw = cfg.get("excluded_from_tip_pool", {}).get("value", "")
    # Multiple names are SEMICOLON-separated (canonical names contain commas:
    # "Krause, Lindsay" must stay as one entry, not split into ["Krause", "Lindsay"]).
    if ";" in permanent_raw:
        permanent = [n.strip() for n in permanent_raw.split(";") if n.strip()]
    else:
        # Single name OR comma-joined canonical (treat as one entry).
        permanent = [permanent_raw.strip()] if permanent_raw.strip() else []
    training: dict[str, str] = {}
    for key, rec in cfg.items():
        if key.startswith("training_excluded:"):
            name = key.split(":", 1)[1].strip()
            training[name] = rec["value"]
    return {"permanent": permanent, "training": training}


def load_config_kv(store: str = "palmetto") -> dict[str, str]:
    """Return the bhaga_model > config tab as a flat {key: value} dict.

    Notes are dropped; use _read_config_tab() if you need them.
    """
    cfg = _read_config_tab(store)
    return {k: v["value"] for k, v in cfg.items()}


def load_full_profile(store: str = "palmetto") -> dict:
    """Build a palmetto.json-shaped dict from the sheet (for shim callers)."""
    pointer = _bootstrap_pointer(store)
    cfg = load_config_kv(store)
    excl = load_exclusions(store)
    aliases = load_aliases(store)
    roster = load_employee_roster(store)

    return {
        "store_id": cfg.get("store_id", pointer.get("store_id", store)),
        "display_name": cfg.get("store", pointer.get("display_name", "")),
        "legal_entity": cfg.get("legal_entity", pointer.get("legal_entity", "")),
        "google_account_key": pointer.get("google_account_key", store),
        "timezone": {
            "shop_tz": cfg.get("shop_timezone", "America/Chicago"),
            "square_account_display_tz": cfg.get(
                "square_account_display_tz", "America/New_York"
            ),
        },
        "google_sheets": pointer.get("google_sheets", {}),
        "google_drive": pointer.get("google_drive", {}),
        "clickup": pointer.get("clickup", {}),
        "square": pointer.get("square", {}),
        "adp_run": pointer.get("adp_run", {}),
        "employees": {
            "aliases": aliases,
            "roster": roster,
            "excluded_from_tip_pool_and_labor_pct": excl["permanent"],
            "training_excluded": excl["training"],
        },
        "shop_hours": {
            "open_local_time": cfg.get("shop_open", "10:00"),
            "close_local_time": cfg.get("shop_close", "21:00"),
        },
        "_source": "bhaga_model sheet via skills/store_profile/reader.py",
    }


def write_alias(
    store: str, raw_name: str, canonical_name: str, *, note: str = ""
) -> None:
    """Add a new alias for `canonical_name` to bhaga_model > employees.

    Used by skills/adp_run_automation/employee_aliases.py when a new hire is
    auto-detected: writes the new raw_name into the existing row's aliases
    cell (or creates a new row if the canonical is new).
    """
    pointer = _bootstrap_pointer(store)
    sid = pointer["google_sheets"]["bhaga_model"]["spreadsheet_id"]
    account = pointer.get("google_account_key", store)
    token = refresh_access_token(account)

    roster = _read_employees_tab(store)
    rows = _fetch_range(sid, "employees!A1:E500", account=account)
    header = [h.strip() for h in rows[0]]
    alias_col = header.index("aliases")
    notes_col = header.index("notes") if "notes" in header else 2

    target_row_idx: Optional[int] = None
    for i, row in enumerate(rows[1:], start=2):
        if row and row[0].strip() == canonical_name:
            target_row_idx = i
            break

    if target_row_idx is None:
        # Append a new row at the end.
        new_row = [canonical_name, raw_name, note]
        rng = urllib.parse.quote(f"employees!A{len(rows)+1}", safe="!:")
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{rng}"
            "?valueInputOption=USER_ENTERED"
        )
        body = json.dumps({"values": [new_row]}).encode()
        req = urllib.request.Request(
            url, data=body, method="PUT",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    else:
        # Update existing aliases cell.
        existing_row = rows[target_row_idx - 1]
        existing_aliases = (
            existing_row[alias_col].strip() if alias_col < len(existing_row) else ""
        )
        parts = (
            [a.strip() for a in existing_aliases.split(";") if a.strip()]
            if ";" in existing_aliases
            else [a.strip() for a in existing_aliases.split(",") if a.strip()]
        )
        if raw_name not in parts:
            parts.append(raw_name)
        new_value = "; ".join(parts)
        col_letter = chr(ord("A") + alias_col)
        rng = urllib.parse.quote(
            f"employees!{col_letter}{target_row_idx}", safe="!:"
        )
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{rng}"
            "?valueInputOption=USER_ENTERED"
        )
        body = json.dumps({"values": [[new_value]]}).encode()
        req = urllib.request.Request(
            url, data=body, method="PUT",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()

    # Invalidate cache so subsequent reads see the new alias.
    _read_employees_tab.cache_clear()
