"""Prove the backfill: what is stored equals what ClickUp holds. Read-only on both sides.

    python3 -m cloud.store_tasks.verify_backfill --store palmetto

Exit 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import sys

from cloud.store_tasks import clickup_import as ci, service as svc, store as st

FIELDS = ("title", "date", "shift", "status", "assignee_id", "completed_at", "instructions", "order",
          "parent_id", "due_at")


def verify(tasks: svc.Tasks, store_id: str, raw: dict[str, list[dict]] | None = None) -> dict:
    cfg = tasks.store(store_id)
    raw = raw or ci.fetch_all(cfg)
    want_docs, want_counts = ci.split(cfg, raw)
    # Days this service generates are its own; only mirrored days are compared.
    want = {d["id"]: d for d in want_docs if not cfg.generated(_day(d))}
    have = {d["id"]: d for d in tasks.docs.query(st.TASKS, store=store_id, source="clickup")
            if not cfg.generated(_day(d))}

    # Straight from ClickUp's payload, independent of the import mapping.
    raw_by_shift = {shift: collections.Counter(
        "done" if (r.get("status") or {}).get("type") == "closed" else "open"
        for r in rows if not ci.is_form(r)) for shift, rows in raw.items()}
    have_by_shift: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for d in have.values():
        have_by_shift[d["shift"]][d["status"]] += 1
    mirrored = len(want) == len(want_docs)

    mismatched = [(i, f) for i, d in want.items() if i in have for f in FIELDS if have[i].get(f) != d.get(f)]
    stored_counts = {c["id"]: c for c in tasks.docs.query(st.COUNTS, store=store_id)}
    count_diffs = [c["id"] for c in want_counts
                   if stored_counts.get(c["id"], {}).get("source") == "clickup"
                   and stored_counts[c["id"]]["items"] != c["items"]]
    raw_photos = len({a["id"] for rows in raw.values() for r in rows if ci.is_form(r)
                      for f in r.get("custom_fields") or [] if f.get("type") == "attachment"
                      for a in f.get("value") or []})
    have_photos = sum(len(c.get("photos") or []) for c in stored_counts.values())

    checks = {
        "every ClickUp task is stored": not (set(want) - set(have)),
        "nothing stored that ClickUp lacks": not (set(have) - set(want)),
        "per-list open/done totals match ClickUp": (not mirrored) or all(
            dict(raw_by_shift[s]) == dict(have_by_shift[s]) for s in raw_by_shift),
        "every field matches on every task": not mismatched,
        "every closing submission day is stored": all(c["id"] in stored_counts for c in want_counts),
        "closing counts match": not count_diffs,
        "every photo is copied": have_photos == raw_photos,
    }
    return {
        "store": store_id, "ok": all(checks.values()), "checks": checks,
        "clickup_tasks": len(want), "stored_tasks": len(have),
        "by_list": {s: dict(c) for s, c in raw_by_shift.items()},
        "closing_days": len(want_counts), "photos": {"clickup": raw_photos, "stored": have_photos},
        "missing": sorted(set(want) - set(have))[:10], "field_mismatches": mismatched[:10],
    }


def _day(doc: dict):
    return datetime.date.fromisoformat(doc["date"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", required=True)
    args = ap.parse_args(argv)
    out = verify(svc.from_env(), args.store)
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
