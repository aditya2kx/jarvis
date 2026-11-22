"""One-time provisioning for the Tasks API (run from a machine with ADC).

    python3 -m cloud.store_tasks.admin seed --store palmetto
    python3 -m cloud.store_tasks.admin manager --store palmetto --name "First Last" --pin 123456
    python3 -m cloud.store_tasks.admin account --store palmetto --email a@b.com --staff "First Last"
    python3 -m cloud.store_tasks.admin account --store palmetto --email ipad@b.com --label "Front iPad"
    python3 -m cloud.store_tasks.admin cutover --store palmetto --date 2026-12-01
    python3 -m cloud.store_tasks.admin photo-rules --store palmetto [--apply]
    python3 -m cloud.store_tasks.admin count-sections --store palmetto --channel 8cr6661-737
    python3 -m cloud.store_tasks.admin link-templates --store palmetto
    python3 -m cloud.store_tasks.admin tracker --store palmetto --name Leadership --admin "First Last" \
        --member "First Last" --member "First Last" --tag Austin --tag Houston
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("seed", "manager", "account", "cutover", "photo-rules", "count-sections", "link-templates",
                 "photo-sizes", "tracker"):
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
        if name == "count-sections":
            sp.add_argument("--channel", required=True, help="ClickUp chat channel ClickUp's closing updates go to")
        if name == "tracker":
            sp.add_argument("--name", required=True, help="what the private list is called")
            sp.add_argument("--admin", action="append", required=True, help="who runs it and chooses its members")
            sp.add_argument("--member", action="append", default=[], help="who else sees it")
            sp.add_argument("--tag", action="append", default=[], help="a store an item can be about")
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
    elif args.cmd == "tracker":
        ids = {s["name"].lower(): s["id"] for s in tasks.staff(args.store)}
        missing = [n for n in args.admin + args.member if n.lower() not in ids]
        if missing:
            print(f"no active person named {missing} in {args.store}", file=sys.stderr)
            return 1
        b = tr.Tracker(tasks).create(args.store, args.name, [ids[n.lower()] for n in args.admin],
                                     [ids[n.lower()] for n in args.member], args.tag, "admin-cli")
        print(f"tracker {b['name']!r}: {len(b['members'])} members, {len(b['admins'])} admin")
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
