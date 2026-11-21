"""One-time provisioning for the Tasks API (run from a machine with ADC).

    python3 -m cloud.store_tasks.admin seed --store palmetto
    python3 -m cloud.store_tasks.admin manager --store palmetto --name "First Last" --pin 123456
    python3 -m cloud.store_tasks.admin account --store palmetto --email a@b.com --staff "First Last"
    python3 -m cloud.store_tasks.admin account --store palmetto --email ipad@b.com --label "Front iPad"
    python3 -m cloud.store_tasks.admin cutover --store palmetto --date 2026-12-01
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import sys

from skills.google_signin import access

from cloud.store_tasks import auth, model, service as svc, store as st

SIGNIN_SERVICE = "store-tasks-app"
STORES_DIR = pathlib.Path(__file__).parent / "stores"


def seed(tasks: svc.Tasks, store_id: str) -> model.StoreConfig:
    """Write the store's config from git. Runtime-owned fields (cutover, count items) are kept."""
    want = json.loads((STORES_DIR / f"{store_id}.json").read_text())
    try:
        cur = tasks.store(store_id)
        want["cutover_date"] = cur.cutover_date or want.get("cutover_date")
        want["count_items"] = list(cur.count_items) or want.get("count_items") or []
    except svc.NotFound:
        pass
    cfg = model.StoreConfig.from_dict(want)
    tasks.put_store(cfg)
    return cfg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("seed", "manager", "account", "cutover"):
        sp = sub.add_parser(name)
        sp.add_argument("--store", required=True)
        if name == "manager":
            sp.add_argument("--name", required=True)
            sp.add_argument("--pin", required=True)
        if name == "account":
            sp.add_argument("--email", required=True, help="Google account allowed to sign in")
            sp.add_argument("--staff", help="the person this account belongs to; omit for a shared store account")
            sp.add_argument("--label", default="")
        if name == "cutover":
            sp.add_argument("--date", required=True, help="first day generated here; 'none' to clear")
    args = ap.parse_args(argv)
    tasks = svc.from_env()

    if args.cmd == "seed":
        cfg = seed(tasks, args.store)
        print(f"store {cfg.id}: {len(cfg.shifts)} shifts, cutover {cfg.cutover_date}")
    elif args.cmd == "manager":
        match = [s for s in tasks.staff(args.store, include_inactive=True)
                 if s["name"].lower() == args.name.lower()]
        s = tasks.save_staff(args.store, {**(match[0] if match else {}), "name": args.name,
                                          "role": auth.MANAGER, "active": True}, "admin-cli")
        tasks.set_pin(args.store, s["id"], args.pin)
        print(f"manager {s['id']} ready")
    elif args.cmd == "account":
        staff_id = None
        if args.staff:
            match = [s for s in tasks.staff(args.store, include_inactive=True) if s["name"].lower() == args.staff.lower()]
            if not match:
                print(f"no person named {args.staff!r} in {args.store}", file=sys.stderr)
                return 1
            staff_id = match[0]["id"]
        acct = tasks.save_account(args.store, {"email": args.email, "staff_id": staff_id, "label": args.label}, "admin-cli")
        print(acct["email"], "Google sign-in:", access.Access(SIGNIN_SERVICE).set(acct["email"], True))
    elif args.cmd == "cutover":
        day = None if args.date == "none" else datetime.date.fromisoformat(args.date).isoformat()
        tasks.put_store(dataclasses.replace(tasks.store(args.store), cutover_date=day))
        # Days before cutover belong to ClickUp. Lists built here during a trial would sit beside
        # the mirrored ones, so they are removed when the date moves later.
        stale = [t for t in tasks.docs.query(st.TASKS, store=args.store)
                 if t.get("source") != "clickup" and (day is None or t["date"] < day)]
        for t in stale:
            tasks.docs.delete(st.TASKS, t["id"])
        print(f"store {args.store}: cutover {day}; removed {len(stale)} trial tasks from ClickUp-owned days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
