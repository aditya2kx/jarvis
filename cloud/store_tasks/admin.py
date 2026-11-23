"""One-time provisioning for the Tasks API (run from a machine with ADC).

    python3 -m cloud.store_tasks.admin seed --store palmetto
    python3 -m cloud.store_tasks.admin manager --store palmetto --name "First Last" --pin 123456
    python3 -m cloud.store_tasks.admin account --store palmetto --email a@b.com --staff "First Last"
    python3 -m cloud.store_tasks.admin account --store palmetto --email ipad@b.com --label "Front iPad"
    python3 -m cloud.store_tasks.admin account --store houston --email a@b.com --staff "First Last"  # a@b.com already in palmetto: adds houston to its switcher
    python3 -m cloud.store_tasks.admin copy-templates --from palmetto --store houston
    python3 -m cloud.store_tasks.admin cutover --store palmetto --date 2026-12-01
    python3 -m cloud.store_tasks.admin photo-rules --store palmetto [--apply]
    python3 -m cloud.store_tasks.admin count-sections --store palmetto --channel 8cr6661-737
    python3 -m cloud.store_tasks.admin link-templates --store palmetto
    python3 -m cloud.store_tasks.admin project --id leadership --name Leadership \
        --admin "First Last=a@b.com" --member "First Last=c@d.com" --tag Austin --tag Houston
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from skills.clickup_chat import runner as clickup_chat
from skills.google_signin import access

from cloud.store_tasks import auth, clickup_import, model, service as svc, store as st, summaries, tracker as tr

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


def copy_templates(tasks: svc.Tasks, src: str, store_id: str) -> int:
    """A new store starts with another store's active task list (subtasks under their copied
    parents) and closing-count fields. Refuses a store that already has tasks."""
    if tasks.templates(store_id):
        raise svc.Invalid(f"{store_id} already has tasks")
    shifts = {s.id for s in tasks.store(store_id).shifts}
    rows = [t for t in tasks.templates(src) if t.active and t.shift in shifts]
    ids: dict[str, str] = {}
    for t in sorted(rows, key=lambda t: t.parent_id is not None):  # parents before their subtasks
        if t.parent_id and t.parent_id not in ids:
            continue
        data = {k: v for k, v in t.to_dict().items() if k not in ("id", "store", "parent_id")}
        ids[t.id] = tasks.save_template(store_id, {**data, "parent_id": ids.get(t.parent_id or "")},
                                        f"copy-templates:{src}").id
    cfg = tasks.store(store_id)
    if not cfg.count_items:
        tasks.put_store(dataclasses.replace(cfg, count_items=tasks.store(src).count_items))
    return len(ids)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("seed", "manager", "account", "cutover", "photo-rules", "count-sections", "link-templates",
                 "photo-sizes", "project", "copy-templates"):
        sp = sub.add_parser(name)
        if name == "project":
            sp.add_argument("--id", required=True, help="the project's address: /p/<id>")
            sp.add_argument("--name", required=True, help="what the project is called")
            sp.add_argument("--admin", action="append", required=True, help='"Name=email": runs it, chooses its members')
            sp.add_argument("--member", action="append", default=[], help='"Name=email": also on it')
            sp.add_argument("--tag", action="append", default=[], help="a store an item can be about")
            continue
        sp.add_argument("--store", required=True)
        if name == "manager":
            sp.add_argument("--name", required=True)
            sp.add_argument("--pin", help="for the shop device; omit for a manager who signs in with Google")
        if name == "copy-templates":
            sp.add_argument("--from", dest="src", required=True, help="the store whose tasks are copied")
        if name == "account":
            sp.add_argument("--email", required=True, help="Google account allowed to sign in")
            sp.add_argument("--staff", help="the person this account belongs to; omit for a shared store account")
            sp.add_argument("--label", default="")
        if name == "cutover":
            sp.add_argument("--date", required=True, help="first day generated here; 'none' to clear")
        if name == "count-sections":
            sp.add_argument("--channel", required=True, help="ClickUp chat channel ClickUp's closing updates go to")
        if name == "photo-rules":
            sp.add_argument("--apply", action="store_true", help="turn on 'needs a photo' for the listed tasks")
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
        if args.pin:
            tasks.set_pin(args.store, s["id"], args.pin)
        print(f"manager {s['id']} ready")
    elif args.cmd == "copy-templates":
        print(f"{args.store}: copied {copy_templates(tasks, args.src, args.store)} tasks from {args.src}")
    elif args.cmd == "account":
        staff_id = None
        if args.staff:
            match = [s for s in tasks.staff(args.store, include_inactive=True) if s["name"].lower() == args.staff.lower()]
            if not match:
                print(f"no person named {args.staff!r} in {args.store}", file=sys.stderr)
                return 1
            staff_id = match[0]["id"]
        home = tasks.docs.get(st.ACCOUNTS, args.email.strip().lower())
        if home and home["store"] != args.store:
            if not staff_id:
                print(f"{args.email} signs in to {home['store']}; give --staff to add {args.store}", file=sys.stderr)
                return 1
            acct = tasks.add_store_access(args.email, args.store, staff_id, "admin-cli")
            print(acct["email"], f"home {acct['store']}, also {sorted(acct['also'])}")
            return 0
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
    elif args.cmd == "count-sections":
        cfg = tasks.store(args.store)
        since = int((tasks.now() - datetime.timedelta(days=3)).timestamp() * 1000)
        updates = [m for m in clickup_chat.fetch_messages(args.channel, since_ts_ms=since)
                   if "Submission task:" in (m.get("content") or "")]
        tz = ZoneInfo(cfg.tz)
        for m in reversed(updates):
            day = datetime.datetime.fromtimestamp(m["date"] / 1000, tz).date()
            count = tasks.count(args.store, day)
            if count:
                items = summaries.sections_from_update(cfg, count, m["content"])
                tasks.put_store(dataclasses.replace(cfg, count_items=items))
                print(f"{sum(1 for i in items if i.get('section'))}/{len(items)} fields placed, from {day}:")
                for i in items:
                    print(f"  {i.get('section') or '-':<16} {i['name']}")
                break
        else:
            print("no closing update with a stored count in the last 3 days", file=sys.stderr)
            return 1
    elif args.cmd == "photo-rules":
        for r in clickup_import.photo_rules(tasks, args.store):
            why = "asks for one" if r["asks"] else f"photos on {r['photo_days']} days, last {r['last_photo']}"
            print(f"{'on ' if r['needs_photo'] else 'off'} {r['shift']:<12} {r['title'][:60]:<60} {why}")
            if args.apply and not r["needs_photo"]:
                cur = tasks.docs.get(st.TEMPLATES, r["id"])
                tasks.save_template(args.store, {**cur, "needs_photo": True}, "photo-rules")
    elif args.cmd == "link-templates":
        # Each ClickUp task points at its recurring definition (how-to, examples), and each definition
        # gets the latest photos ClickUp holds for it ("photos from earlier days").
        cfg = tasks.store(args.store)
        print(f"linked {clickup_import.link_templates(tasks, cfg)} tasks; "
              f"earlier photos on {clickup_import.seed_recent_photos(tasks, cfg)} definitions")
    elif args.cmd == "project":
        people = [dict(zip(("name", "email"), x.rsplit("=", 1))) for x in args.admin + args.member]
        known = {e: m["id"] for m in (tasks.docs.get(st.TRACKERS, args.id) or {}).get("members", []) for e in m["emails"]}
        for n, person in enumerate(people):
            person["id"] = known.get(person["email"].strip().lower(), f"m{n + 1}")  # a re-run keeps owners intact
        p = tr.Tracker(tasks).create(args.id, args.name, people, [x["id"] for x in people[:len(args.admin)]],
                                     args.tag, "admin-cli")
        for person in people:
            print(person["name"], "Google sign-in:", access.Access(SIGNIN_SERVICE).set(person["email"].lower(), True))
        print(f"project {p['name']!r} at /p/{p['id']}: {len(p['members'])} members")
    elif args.cmd == "photo-sizes":
        print(f"made smaller copies of {photo_sizes(tasks, args.store)} photos")
    return 0


def photo_sizes(tasks: svc.Tasks, store_id: str) -> int:
    """Thumbnail and screen-size copies of every stored photo, so no one waits on the first view.
    Safe to re-run: a copy that exists is read, not remade."""
    # Not every path has an extension; ``photo`` leaves anything that is not a photo as it is.
    images = [p for p in tasks.photos.paths(f"{store_id}/") if f"/{svc.SIZED_DIR}/" not in p]
    def both(path: str) -> None:
        for size in svc.PHOTO_SIZES:
            try:
                tasks.photo(path, size)
            except Exception as e:  # one unreadable photo must not stop the rest
                print(f"store-tasks fail reason=photo_size path={path} size={size} err={e!r}", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(both, images))
    return len(images)


if __name__ == "__main__":
    sys.exit(main())
