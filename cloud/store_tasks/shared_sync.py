"""Shared ADP data from BigQuery (read-only): the schedule → ``shared_shifts``, and who is employed.

The warehouse stays the system of record. This service keeps a small, fast copy of the days the
staff app shows, so the tablet never waits on BigQuery, and marks each person ``adp_active`` from
the latest ADP directory snapshot: only they can be picked to do a task.

    python3 -m cloud.store_tasks.shared_sync --store palmetto
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

from google.cloud import bigquery

from cloud.store_tasks import model, service as svc, store as st

DAYS_BACK = 7
DAYS_AHEAD = 14


def read_schedule(dataset: str, start: datetime.date, end: datetime.date) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9_]+", dataset):
        raise ValueError(f"bad dataset {dataset!r}")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or None
    client = bigquery.Client(project=project)
    job = client.query(
        f"SELECT date, employee_name, shift_ranges_json FROM `{client.project}.{dataset}.adp_scheduled_shifts` "
        "WHERE date BETWEEN @a AND @b AND hour_kind = 'shift'",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("a", "DATE", start),
            bigquery.ScalarQueryParameter("b", "DATE", end),
        ]),
    )
    return [{"date": r["date"].isoformat(), "name": r["employee_name"],
             "ranges": json.loads(r["shift_ranges_json"] or "[]")} for r in job.result()]


def read_roster(dataset: str, store_id: str) -> list[str]:
    """Names of everyone Active in the latest ADP directory snapshot (``Last, First``)."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", dataset):
        raise ValueError(f"bad dataset {dataset!r}")
    client = bigquery.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)
    job = client.query(
        f"SELECT DISTINCT employee_name FROM `{client.project}.{dataset}.adp_directory_status` "
        "WHERE store = @store AND employment_status = 'Active' "
        "QUALIFY scraped_at_utc = MAX(scraped_at_utc) OVER ()",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("store", "STRING", store_id)]),
    )
    return [r["employee_name"] for r in job.result()]


def display_name(adp_name: str) -> str:
    """'Guerrero, Amy' → 'Amy Guerrero'."""
    last, _, first = adp_name.partition(",")
    return f"{first.strip()} {last.strip()}".strip() if first else adp_name.strip()


def mark_active(tasks: svc.Tasks, store_id: str, roster: list[str]) -> dict:
    """Flag each person by whether ADP employs them; add anyone employed who is not here yet."""
    if not roster:
        return {"skipped": "empty ADP roster"}  # a failed scrape must not make everyone unassignable
    staff = tasks.staff(store_id, include_inactive=True)
    active: set[str] = set()
    added = 0
    for name in roster:
        s = model.match_staff(name, staff)
        if s is None:
            s = tasks.save_staff(store_id, {"name": display_name(name), "adp_name": name}, "adp-sync")
            staff.append(s)
            added += 1
        active.add(s["id"])
    for s in staff:
        if s.get("adp_active") != (s["id"] in active):
            tasks.docs.set(st.STAFF, s["id"], {"adp_active": s["id"] in active}, merge=True)
    return {"adp_active": len(active), "added": added}


def to_docs(store_id: str, rows: list[dict], start: datetime.date, end: datetime.date, stamp: str) -> list[dict]:
    """One document per day in the window — an empty day is written too, so removals propagate."""
    people: dict[str, dict[str, list]] = {}
    for r in rows:
        ranges = [list(x) for x in model.parse_ranges(r["ranges"])]
        if ranges:
            people.setdefault(r["date"], {}).setdefault(r["name"], []).extend(ranges)
    docs, day = [], start
    while day <= end:
        d = day.isoformat()
        docs.append({"id": f"{store_id}_{d}", "store": store_id, "date": d, "synced_at": stamp,
                     "people": [{"name": n, "ranges": sorted(rs)} for n, rs in sorted(people.get(d, {}).items())]})
        day += datetime.timedelta(days=1)
    return docs


def sync(tasks: svc.Tasks, store_id: str, read=read_schedule, roster=read_roster) -> dict:
    cfg = tasks.store(store_id)
    if not cfg.shifts_dataset:
        return {"store": store_id, "days": 0, "skipped": "no shifts_dataset"}
    today = cfg.today(tasks.now())
    start, end = today - datetime.timedelta(days=DAYS_BACK), today + datetime.timedelta(days=DAYS_AHEAD)
    docs = to_docs(store_id, read(cfg.shifts_dataset, start, end), start, end, tasks.now().isoformat())
    tasks.docs.set_many(st.SHIFTS, docs)
    return {"store": store_id, "days": len(docs), "people_days": sum(len(d["people"]) for d in docs),
            **mark_active(tasks, store_id, roster(cfg.shifts_dataset, store_id))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", required=True)
    args = ap.parse_args(argv)
    print(json.dumps(sync(svc.from_env(), args.store)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
