#!/usr/bin/env python3
"""Provision BHAGA sandbox sheets for per-PR e2e via a *pre-created pool*.

WHY A POOL (and not create-per-PR):
  A service account on a consumer Google account can *edit* sheets that are
  shared with it (this is exactly what the nightly Cloud Run job does), but it
  **cannot create Drive files** — ``spreadsheets.create`` returns 403
  PERMISSION_DENIED because a service account has no personal Drive storage and
  consumer accounts have no Shared Drives. So the per-PR e2e cannot create fresh
  sheets with the SA identity.

  Instead we pre-create a small **pool** of sandbox workbooks *as the operator*
  (a real user, who can create files), share each with the SA as writer, and
  record their IDs in a committed registry (``sandbox_pool.json`` — just sheet
  IDs, non-secret). Each PR then **leases a free slot**, the CI job *clears and
  rewrites* that slot's sheets (an edit the SA can do), runs the e2e, and
  releases the lease. No Drive create/delete ever happens in CI.

ACTIONS:
  * ``create-pool`` (operator, one-time / occasional) — create N slots × 4
    workbooks as the user, share with the SA, write the registry. Requires a
    user credential (NOT the SA): run it locally, never in CI.
  * ``destroy-pool`` (operator) — delete every pool sheet (cleanup).
  * ``provision`` (CI, per run) — lease a free slot, clear stale tabs, re-seed
    the model ``config``/``employees`` (read-only from prod) + raw headers, and
    emit ``BHAGA_STAGING_*_SID`` so ``resolve_sheet_id`` routes the pipeline to
    the leased slot.
  * ``teardown`` (CI, on PR close) — clear the slot's tabs and release the lease.
    The sheets persist (they're pool members).

SLOT LEASING:
  In CI (``BHAGA_STATE_BACKEND=firestore``) a Firestore transaction atomically
  leases a *free* slot from the pool (reclaiming leases older than the TTL so a
  crashed run can't wedge a slot forever). Without Firestore (local runs) it
  falls back to a deterministic ``slot = pr_number % num_slots`` — simple and
  good enough when you're the only caller.

ISOLATION GUARANTEES:
  * The only prod access is a *read* of the prod model ``config`` + ``employees``
    tabs (to seed realistic store metadata). No prod write ever.
  * Emitting all four staging IDs means ``resolve_sheet_id`` never falls back to
    a prod ID — and the ``BHAGA_SHEET_MODE=staging`` guard would hard-block it
    anyway.
  * No Square / ADP / ClickUp / OTP code is imported or invoked here.

Usage:
    # one-time, as the operator (user creds, can create Drive files):
    python3 -m agents.bhaga.scripts.sandbox_provision --store palmetto \
        --action create-pool --slots 3

    # per run (CI, service account):
    python3 -m agents.bhaga.scripts.sandbox_provision --store palmetto \
        --pr-number 42 --action provision --emit-env-file "$GITHUB_ENV"
    python3 -m agents.bhaga.scripts.sandbox_provision --store palmetto \
        --pr-number 42 --action teardown
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.bootstrap_sheets import (  # noqa: E402
    DRIVE_API,
    SHEETS_API,
    api_request,
    create_folder_at_root,
    create_spreadsheet,
    find_folder_at_root,
    find_spreadsheet_in_folder,
    move_file_into_folder,
    seed_tab_headers,
)
from agents.bhaga.scripts.share_sheets_with_sa import SERVICE_ACCOUNT, _share_file  # noqa: E402
from core.config_loader import refresh_access_token  # noqa: E402
from skills.bhaga_config import state_adapter  # noqa: E402
from skills.tip_ledger_writer.schema import WORKBOOK_SCHEMAS  # noqa: E402

SANDBOX_FOLDER_NAME = "BHAGA-sandbox"
DEFAULT_SLOTS = 3
SLOT_COLLECTION = "sandbox_slots"
# A lease older than this is considered abandoned (crashed run) and reclaimable.
LEASE_TTL_SECONDS = 45 * 60

# The four profile keys resolve_sheet_id understands. Each gets its own
# spreadsheet per slot so staging mode never falls back to a prod ID.
PROFILE_KEYS: tuple[str, ...] = (
    "bhaga_model",
    "bhaga_adp_raw",
    "bhaga_square_raw",
    "bhaga_review_raw",
)

# Seed tabs for the two sheets WORKBOOK_SCHEMAS doesn't define (the model's
# config/employees seeds, and the review raw sheet). The model's data tabs are
# created on demand by update_model_sheet's _upsert_tab.
_MODEL_SEED_TABS: list[dict] = [
    {"tab_name": "config", "header": ["key", "value", "notes"]},
    {"tab_name": "employees", "header": ["canonical_name", "aliases", "notes"]},
]
_REVIEW_SEED_TABS: list[dict] = [
    {"tab_name": "reviews", "header": ["review_id", "date_local", "rating", "text"]},
    {"tab_name": "config", "header": ["key", "value", "notes"]},
]

POOL_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_pool.json")


# ── Pure helpers (no I/O — unit-tested) ───────────────────────────


def staging_env_key(profile_key: str) -> str:
    """Map a profile key to the env var resolve_sheet_id looks up.

    e.g. "bhaga_model" -> "BHAGA_STAGING_BHAGA_MODEL_SID".
    """
    return f"BHAGA_STAGING_{profile_key.upper()}_SID"


def slot_title(slot: int, profile_key: str) -> str:
    """Deterministic, slot-scoped spreadsheet title (so the pool is reusable)."""
    return f"BHAGA-sandbox slot{slot} {profile_key}"


def all_slot_titles(slot: int) -> dict[str, str]:
    """{profile_key: title} for every sheet a slot owns."""
    return {key: slot_title(slot, key) for key in PROFILE_KEYS}


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


def deterministic_slot(pr_number: int, num_slots: int) -> int:
    """Local/no-Firestore fallback: map a PR number to a fixed slot."""
    if num_slots < 1:
        raise ValueError(f"num_slots must be >= 1, got {num_slots}")
    return pr_number % num_slots


def _lease_is_stale(leased_at: str | None, now: datetime.datetime, ttl_seconds: int) -> bool:
    """True if a lease timestamp is older than the TTL (so it can be reclaimed)."""
    if not leased_at:
        return True
    try:
        ts = datetime.datetime.fromisoformat(leased_at)
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return (now - ts).total_seconds() > ttl_seconds


def slot_ids_from_registry(registry: dict, slot: int) -> dict[str, str]:
    """Pull {profile_key: spreadsheet_id} for a slot out of the registry dict."""
    for entry in registry.get("slots", []):
        if entry.get("slot") == slot:
            return {key: entry[key] for key in PROFILE_KEYS}
    raise KeyError(f"slot {slot} not found in pool registry (have "
                   f"{[e.get('slot') for e in registry.get('slots', [])]})")


# ── Registry I/O ──────────────────────────────────────────────────


def load_registry(path: str = POOL_REGISTRY_PATH) -> dict:
    """Load the committed pool registry (sheet IDs per slot)."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"sandbox pool registry not found at {path}. "
            f"Create the pool first: python3 -m agents.bhaga.scripts.sandbox_provision "
            f"--action create-pool --slots {DEFAULT_SLOTS}"
        )
    with open(path) as f:
        return json.load(f)


def save_registry(registry: dict, path: str = POOL_REGISTRY_PATH) -> None:
    with open(path, "w") as f:
        json.dump(registry, f, indent=2, sort_keys=True)
        f.write("\n")


# ── Thin Google Sheets/Drive I/O ──────────────────────────────────


def _load_pointer(store: str) -> dict:
    """Load the local bootstrap pointer JSON (prod sheet IDs + account key)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "knowledge-base", "store-profiles", f"{store}.json")
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
    url = f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{rng}"
    resp = api_request(url, token)
    return resp.get("values", [])


def _write_values(token: str, spreadsheet_id: str, range_a1: str, values: list[list]) -> None:
    rng = urllib.parse.quote(range_a1, safe="!:")
    url = (
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{rng}"
        "?valueInputOption=RAW"
    )
    api_request(url, token, method="PUT", data={"values": values})


def _list_tab_titles(token: str, spreadsheet_id: str) -> list[str]:
    url = f"{SHEETS_API}/spreadsheets/{spreadsheet_id}?fields=sheets.properties.title"
    resp = api_request(url, token)
    return [s["properties"]["title"] for s in resp.get("sheets", [])]


def _batch_clear(token: str, spreadsheet_id: str, ranges: list[str]) -> None:
    if not ranges:
        return
    url = f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values:batchClear"
    api_request(url, token, method="POST", data={"ranges": ranges})


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


def clear_slot(token: str, slot_ids: dict[str, str]) -> None:
    """Clear stale values from every tab in a slot, then re-seed headers.

    Gives each run a clean slate without deleting/creating sheets. Leftover tabs
    that a previous run's update_model_sheet added remain as empty tabs (their
    values are cleared); the next run's upsert repopulates the ones it needs.
    """
    for key, sid in slot_ids.items():
        titles = _list_tab_titles(token, sid)
        _batch_clear(token, sid, titles)
        # Re-seed the header rows for the tabs we manage (they still exist after
        # a values clear). update_model_sheet creates its own data tabs as needed.
        seed_tab_headers(token, sid, _tab_specs_for(key))


# ── Slot leasing (Firestore in CI, deterministic fallback locally) ─


def acquire_slot(pr_number: int, num_slots: int, *, ttl_seconds: int = LEASE_TTL_SECONDS,
                 attempts: int = 6, backoff_seconds: float = 30.0) -> int:
    """Lease a free pool slot for this PR. Returns the slot index.

    Firestore-backed (atomic) when BHAGA_STATE_BACKEND=firestore; otherwise a
    deterministic pr%num_slots assignment for local single-caller runs.
    """
    if state_adapter._state_backend() != "firestore":
        return deterministic_slot(pr_number, num_slots)

    from google.cloud import firestore  # local import; only needed in CI

    client = state_adapter._get_firestore_client()

    def _try_once() -> int | None:
        transaction = client.transaction()

        @firestore.transactional
        def _txn(txn) -> int | None:
            now = datetime.datetime.now(datetime.timezone.utc)
            refs = [client.collection(SLOT_COLLECTION).document(str(s)) for s in range(num_slots)]
            snaps = [ref.get(transaction=txn) for ref in refs]  # all reads first
            free = reentrant = stale = None
            for s, snap in enumerate(snaps):
                data = snap.to_dict() or {}
                leased_by = data.get("leased_by")
                if leased_by == pr_number:
                    reentrant = s
                    break
                if leased_by is None and free is None:
                    free = s
                elif leased_by is not None and stale is None and _lease_is_stale(
                    data.get("leased_at"), now, ttl_seconds
                ):
                    stale = s
            chosen = reentrant if reentrant is not None else (free if free is not None else stale)
            if chosen is None:
                return None
            txn.set(refs[chosen], {
                "leased_by": pr_number,
                "leased_at": now.isoformat(),
            })
            return chosen

        return _txn(transaction)

    for attempt in range(attempts):
        slot = _try_once()
        if slot is not None:
            return slot
        if attempt < attempts - 1:
            time.sleep(backoff_seconds)
    raise RuntimeError(
        f"all {num_slots} sandbox slots are busy after {attempts} attempts; "
        f"increase the pool size or retry the PR run."
    )


def release_slot(pr_number: int, slot: int) -> None:
    """Release a slot lease iff this PR holds it (idempotent, no-op locally)."""
    if state_adapter._state_backend() != "firestore":
        return
    client = state_adapter._get_firestore_client()
    ref = client.collection(SLOT_COLLECTION).document(str(slot))
    snap = ref.get()
    if (snap.to_dict() or {}).get("leased_by") == pr_number:
        ref.set({"leased_by": None, "leased_at": None})


# ── Pool lifecycle (operator, user creds — never the SA) ───────────


def create_pool(*, store: str, num_slots: int, sa_email: str = SERVICE_ACCOUNT) -> dict:
    """Create N slots × 4 workbooks as the user, share each with the SA, save registry.

    Idempotent: reuses any pool sheet that already exists (matched by title).
    Must run with a *user* credential — service accounts can't create Drive files.
    """
    pointer = _load_pointer(store)
    account = pointer.get("google_account_key", store)
    token = refresh_access_token(account=account)

    folder_id = find_folder_at_root(token, SANDBOX_FOLDER_NAME)
    if not folder_id:
        folder_id = create_folder_at_root(token, SANDBOX_FOLDER_NAME)
        print(f"  created sandbox folder {SANDBOX_FOLDER_NAME!r}: {folder_id}")
    else:
        print(f"  found sandbox folder {SANDBOX_FOLDER_NAME!r}: {folder_id}")

    slots: list[dict] = []
    for s in range(num_slots):
        entry: dict = {"slot": s}
        for key, title in all_slot_titles(s).items():
            existing = find_spreadsheet_in_folder(token, folder_id, title)
            if existing:
                sid = existing
                print(f"  reuse  slot{s} {key}: {sid} ({title!r})")
            else:
                specs = _tab_specs_for(key)
                info = create_spreadsheet(token, title, specs)
                sid = info["spreadsheetId"]
                move_file_into_folder(token, sid, folder_id)
                seed_tab_headers(token, sid, specs)
                print(f"  create slot{s} {key}: {sid} ({title!r})")
            share = _share_file(sid, sa_email, "writer", token)
            if share.get("error"):
                print(f"    WARN share failed for {sid}: HTTP {share['code']} {share['body'][:120]}")
            entry[key] = sid
        slots.append(entry)

    registry = {
        "store": store,
        "folder_id": folder_id,
        "sa_email": sa_email,
        "num_slots": num_slots,
        "slots": slots,
    }
    save_registry(registry)
    print(f"  wrote registry: {POOL_REGISTRY_PATH}")
    return registry


def destroy_pool(*, store: str, registry_path: str = POOL_REGISTRY_PATH) -> dict:
    """Delete every pool sheet listed in the registry (operator cleanup)."""
    registry = load_registry(registry_path)
    pointer = _load_pointer(store)
    account = pointer.get("google_account_key", store)
    token = refresh_access_token(account=account)

    deleted: list[str] = []
    for entry in registry.get("slots", []):
        for key in PROFILE_KEYS:
            sid = entry.get(key)
            if not sid:
                continue
            _delete_file(token, sid)
            deleted.append(sid)
            print(f"  deleted slot{entry.get('slot')} {key}: {sid}")
    return {"deleted": deleted}


# ── Per-run provision / teardown (CI, SA creds) ───────────────────


def _run_token(store: str) -> tuple[str, dict]:
    pointer = _load_pointer(store)
    account = pointer.get("google_account_key", store)
    return refresh_access_token(account=account), pointer


def provision(*, store: str, pr_number: int) -> dict:
    """Lease a free slot, clear it, re-seed model metadata. Returns IDs + meta."""
    registry = load_registry()
    num_slots = registry.get("num_slots", len(registry.get("slots", [])))
    token, pointer = _run_token(store)

    slot = acquire_slot(pr_number, num_slots)
    ids = slot_ids_from_registry(registry, slot)
    print(f"  leased slot {slot} for PR#{pr_number}: {ids}")

    clear_slot(token, ids)
    print(f"  cleared slot {slot} tabs + re-seeded headers")

    seed_counts = seed_model_metadata(
        token,
        prod_model_sid=_prod_model_sid(pointer),
        sandbox_model_sid=ids["bhaga_model"],
    )
    print(f"  seeded model metadata from prod (read-only): {seed_counts}")

    return {
        "pr_number": pr_number,
        "slot": slot,
        "folder_id": registry.get("folder_id"),
        "ids": ids,
        "staging_env": staging_env(ids),
        "seed_counts": seed_counts,
    }


def teardown(*, store: str, pr_number: int) -> dict:
    """Clear the PR's leased slot and release the lease. Sheets persist."""
    registry = load_registry()
    num_slots = registry.get("num_slots", len(registry.get("slots", [])))
    token, _ = _run_token(store)

    # We don't track the leased slot across jobs, so locate it the same way the
    # lease did. With Firestore, find the slot this PR currently holds; locally,
    # the deterministic mapping tells us.
    slot = _slot_held_by(pr_number, num_slots)
    if slot is None:
        print(f"  no slot currently leased by PR#{pr_number}; nothing to release")
        return {"pr_number": pr_number, "slot": None, "released": False}

    ids = slot_ids_from_registry(registry, slot)
    clear_slot(token, ids)
    release_slot(pr_number, slot)
    print(f"  cleared + released slot {slot} for PR#{pr_number}")
    return {"pr_number": pr_number, "slot": slot, "released": True}


def _slot_held_by(pr_number: int, num_slots: int) -> int | None:
    """Find which slot a PR currently holds (Firestore), or the deterministic one."""
    if state_adapter._state_backend() != "firestore":
        return deterministic_slot(pr_number, num_slots)
    client = state_adapter._get_firestore_client()
    for s in range(num_slots):
        snap = client.collection(SLOT_COLLECTION).document(str(s)).get()
        if (snap.to_dict() or {}).get("leased_by") == pr_number:
            return s
    return None


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--pr-number", type=int, default=0)
    cli.add_argument(
        "--action",
        choices=["provision", "teardown", "create-pool", "destroy-pool"],
        required=True,
    )
    cli.add_argument("--slots", type=int, default=DEFAULT_SLOTS,
                     help="Pool size for create-pool (default: %(default)s).")
    cli.add_argument(
        "--emit-env-file", default=None,
        help="Append BHAGA_STAGING_*_SID lines to this file (e.g. $GITHUB_ENV). "
             "Provision only.",
    )
    args = cli.parse_args(argv)

    if args.action == "create-pool":
        result = create_pool(store=args.store, num_slots=args.slots)
        print(json.dumps({"num_slots": result["num_slots"],
                          "folder_id": result["folder_id"]}, indent=2))
    elif args.action == "destroy-pool":
        result = destroy_pool(store=args.store)
        print(json.dumps(result, indent=2))
    elif args.action == "provision":
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
    else:  # teardown
        result = teardown(store=args.store, pr_number=args.pr_number)
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
