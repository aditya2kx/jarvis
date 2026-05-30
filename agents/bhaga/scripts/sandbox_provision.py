#!/usr/bin/env python3
"""Provision / tear down ephemeral BHAGA sandbox sheets for per-PR e2e.

Creates four throwaway Google Sheets (model + 3 raw) inside a dedicated
``BHAGA-sandbox`` Drive folder, seeds the model's ``config`` + ``employees``
tabs by copying them (read-only) from the production model sheet, and emits the
four spreadsheet IDs as ``BHAGA_STAGING_*_SID`` so ``resolve_sheet_id()`` routes
the downstream pipeline to the sandbox instead of prod.

Isolation guarantees:
  * The ONLY production access is a *read* of the prod model ``config`` +
    ``employees`` tabs (to seed realistic store metadata). No prod write ever.
  * Emitting all four staging IDs means ``resolve_sheet_id`` never falls back to
    a prod ID — and the ``BHAGA_SHEET_MODE=staging`` guard (``_assert_not_production_sheet``)
    would hard-block it anyway if it tried.
  * No Square / ADP / ClickUp / OTP code is imported or invoked here.

Used by ``.github/workflows/sandbox-e2e.yml`` (provision) and
``.github/workflows/sandbox-teardown.yml`` (teardown-by-PR-number on close).

Usage:
    python3 -m agents.bhaga.scripts.sandbox_provision --store palmetto \
        --pr-number 42 --action provision --emit-env-file "$GITHUB_ENV"
    python3 -m agents.bhaga.scripts.sandbox_provision --store palmetto \
        --pr-number 42 --action teardown
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.bootstrap_sheets import (  # noqa: E402
    DRIVE_API,
    api_request,
    create_folder_at_root,
    create_spreadsheet,
    find_folder_at_root,
    find_spreadsheet_in_folder,
    move_file_into_folder,
    seed_tab_headers,
)
from core.config_loader import refresh_access_token  # noqa: E402
from skills.tip_ledger_writer.schema import WORKBOOK_SCHEMAS  # noqa: E402

SANDBOX_FOLDER_NAME = "BHAGA-sandbox"

# The four profile keys resolve_sheet_id understands. Each gets its own
# ephemeral spreadsheet so staging mode never falls back to a prod ID.
PROFILE_KEYS: tuple[str, ...] = (
    "bhaga_model",
    "bhaga_adp_raw",
    "bhaga_square_raw",
    "bhaga_review_raw",
)

# Minimal tab specs for the two sheets WORKBOOK_SCHEMAS doesn't define
# (the model's config/employees seeds, and the review raw sheet). The model's
# data tabs are created on demand by update_model_sheet's _upsert_tab.
_MODEL_SEED_TABS: list[dict] = [
    {"tab_name": "config", "header": ["key", "value", "notes"]},
    {"tab_name": "employees", "header": ["canonical_name", "aliases", "notes"]},
]
_REVIEW_SEED_TABS: list[dict] = [
    {"tab_name": "reviews", "header": ["review_id", "date_local", "rating", "text"]},
    {"tab_name": "config", "header": ["key", "value", "notes"]},
]


# ── Pure helpers (no I/O — unit-tested) ───────────────────────────


def staging_env_key(profile_key: str) -> str:
    """Map a profile key to the env var resolve_sheet_id looks up.

    e.g. "bhaga_model" -> "BHAGA_STAGING_BHAGA_MODEL_SID".
    """
    return f"BHAGA_STAGING_{profile_key.upper()}_SID"


def sandbox_title(pr_number: int, profile_key: str) -> str:
    """Deterministic, PR-scoped spreadsheet title (so teardown can find it).

    Deterministic on (pr_number, profile_key) so the teardown job can locate
    the exact sheets to delete using only the PR number — no cross-job state.
    """
    return f"BHAGA-sandbox PR#{pr_number} {profile_key}"


def all_sandbox_titles(pr_number: int) -> dict[str, str]:
    """{profile_key: title} for every sheet a PR's sandbox owns."""
    return {key: sandbox_title(pr_number, key) for key in PROFILE_KEYS}


def staging_env(ids: dict[str, str]) -> dict[str, str]:
    """{profile_key: spreadsheet_id} -> {BHAGA_STAGING_*_SID: spreadsheet_id}."""
    return {staging_env_key(key): sid for key, sid in ids.items()}


def render_env_file(env: dict[str, str]) -> str:
    """Render a dict as KEY=value lines for a GITHUB_ENV-style file."""
    return "".join(f"{k}={v}\n" for k, v in sorted(env.items()))


def _tab_specs_for(profile_key: str) -> list[dict]:
    """Header/tab specs used when first creating each sandbox spreadsheet."""
    if profile_key == "bhaga_model":
        return _MODEL_SEED_TABS
    if profile_key == "bhaga_adp_raw":
        return WORKBOOK_SCHEMAS["BHAGA ADP Raw"]
    if profile_key == "bhaga_square_raw":
        return WORKBOOK_SCHEMAS["BHAGA Square Raw"]
    if profile_key == "bhaga_review_raw":
        return _REVIEW_SEED_TABS
    raise KeyError(f"unknown profile key {profile_key!r}")


# ── Thin Google Sheets/Drive I/O ──────────────────────────────────


def _load_pointer(store: str) -> dict:
    """Load the local bootstrap pointer JSON (prod sheet IDs + account key)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(
        here, "..", "knowledge-base", "store-profiles", f"{store}.json"
    )
    with open(path) as f:
        return json.load(f)


def _prod_model_sid(pointer: dict) -> str:
    """Prod model sheet id straight from the pointer (NOT via resolve_sheet_id).

    Read-only seed source. We bypass resolve_sheet_id deliberately so this never
    accidentally returns a staging id during provisioning.
    """
    return pointer["google_sheets"]["bhaga_model"]["spreadsheet_id"]


def _read_values(token: str, spreadsheet_id: str, range_a1: str) -> list[list]:
    rng = urllib.parse.quote(range_a1, safe="!:")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{rng}"
    resp = api_request(url, token)
    return resp.get("values", [])


def _write_values(token: str, spreadsheet_id: str, range_a1: str, values: list[list]) -> None:
    rng = urllib.parse.quote(range_a1, safe="!:")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{rng}"
        "?valueInputOption=RAW"
    )
    api_request(url, token, method="PUT", data={"values": values})


def _delete_file(token: str, file_id: str) -> None:
    api_request(f"{DRIVE_API}/files/{file_id}", token, method="DELETE")


def _col_a1(n_cols: int) -> str:
    from agents.bhaga.scripts.bootstrap_sheets import _col_letter
    return _col_letter(max(1, n_cols))


def seed_model_metadata(token: str, *, prod_model_sid: str, sandbox_model_sid: str) -> dict:
    """Copy the prod model's config + employees tabs into the sandbox model.

    This is the only production access in the whole flow, and it is read-only.
    Returns {"config_rows": n, "employees_rows": n} for evidence.
    """
    counts: dict[str, int] = {}
    # Bounded read ranges keep the seed cheap. If the prod model ever grows the
    # config tab past column F, or employees past ~499 rows, widen these — a
    # silent truncation here would seed an incomplete sandbox profile.
    for tab, read_range in (("config", "config!A1:F200"), ("employees", "employees!A1:E500")):
        values = _read_values(token, prod_model_sid, read_range)
        if not values:
            counts[f"{tab}_rows"] = 0
            continue
        last_col = _col_a1(max(len(r) for r in values))
        write_range = f"{tab}!A1:{last_col}{len(values)}"
        _write_values(token, sandbox_model_sid, write_range, values)
        counts[f"{tab}_rows"] = max(0, len(values) - 1)
    return counts


def provision(*, store: str, pr_number: int) -> dict:
    """Create the four sandbox sheets and seed model metadata. Returns IDs + meta."""
    pointer = _load_pointer(store)
    account = pointer.get("google_account_key", store)
    token = refresh_access_token(account=account)

    folder_id = find_folder_at_root(token, SANDBOX_FOLDER_NAME)
    if not folder_id:
        folder_id = create_folder_at_root(token, SANDBOX_FOLDER_NAME)
        print(f"  created sandbox folder {SANDBOX_FOLDER_NAME!r}: {folder_id}")
    else:
        print(f"  found sandbox folder {SANDBOX_FOLDER_NAME!r}: {folder_id}")

    titles = all_sandbox_titles(pr_number)
    ids: dict[str, str] = {}
    for key, title in titles.items():
        # Idempotent: reuse if a prior provision for this PR already created it.
        existing = find_spreadsheet_in_folder(token, folder_id, title)
        if existing:
            print(f"  reuse {key}: {existing} ({title!r})")
            ids[key] = existing
            continue
        specs = _tab_specs_for(key)
        info = create_spreadsheet(token, title, specs)
        sid = info["spreadsheetId"]
        move_file_into_folder(token, sid, folder_id)
        seed_tab_headers(token, sid, specs)
        ids[key] = sid
        print(f"  created {key}: {sid} ({title!r})")

    seed_counts = seed_model_metadata(
        token,
        prod_model_sid=_prod_model_sid(pointer),
        sandbox_model_sid=ids["bhaga_model"],
    )
    print(f"  seeded model metadata from prod (read-only): {seed_counts}")

    return {
        "pr_number": pr_number,
        "folder_id": folder_id,
        "ids": ids,
        "staging_env": staging_env(ids),
        "seed_counts": seed_counts,
    }


def teardown(*, store: str, pr_number: int) -> dict:
    """Delete every sandbox sheet owned by a PR (by deterministic title)."""
    pointer = _load_pointer(store)
    account = pointer.get("google_account_key", store)
    token = refresh_access_token(account=account)

    folder_id = find_folder_at_root(token, SANDBOX_FOLDER_NAME)
    if not folder_id:
        print(f"  no sandbox folder {SANDBOX_FOLDER_NAME!r}; nothing to tear down")
        return {"pr_number": pr_number, "deleted": []}

    deleted: list[str] = []
    for key, title in all_sandbox_titles(pr_number).items():
        sid = find_spreadsheet_in_folder(token, folder_id, title)
        if not sid:
            continue
        _delete_file(token, sid)
        deleted.append(sid)
        print(f"  deleted {key}: {sid} ({title!r})")
    if not deleted:
        print(f"  no sandbox sheets found for PR#{pr_number}")
    return {"pr_number": pr_number, "deleted": deleted}


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--pr-number", type=int, required=True)
    cli.add_argument("--action", choices=["provision", "teardown"], required=True)
    cli.add_argument(
        "--emit-env-file", default=None,
        help="Append BHAGA_STAGING_*_SID lines to this file (e.g. $GITHUB_ENV). "
             "Provision only.",
    )
    args = cli.parse_args(argv)

    if args.action == "provision":
        result = provision(store=args.store, pr_number=args.pr_number)
        env_text = render_env_file(result["staging_env"])
        target = args.emit_env_file or os.environ.get("GITHUB_ENV")
        if target:
            with open(target, "a") as f:
                f.write(env_text)
            print(f"  emitted {len(result['staging_env'])} staging env var(s) -> {target}")
        print("# staging env:")
        print(env_text, end="")
        print("# result:")
        print(json.dumps(result, indent=2))
    else:
        result = teardown(store=args.store, pr_number=args.pr_number)
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
