#!/usr/bin/env python3
"""Clear every ADP shift in a date range — the console's Clear weeks → Apply, for agents.

Reads the same BQ rows the Labor page shows (`adp_scheduled_shifts` through
`employee_aliases`, PTO excluded; `adp_open_shifts`), queues a `shift-delete` per assigned
shift (published or draft) and an `open-delete` per open shift as one Apply batch, then starts
the ADP Apply job on it. Dates must be after today (America/Chicago). Run by
`/prod-ops clear-weeks <from> <through> [limit=N] [dry-run]`: `limit=N` deletes only the first N
assigned shifts (a canary), `dry-run` prints the counts and queues nothing. Output carries
counts only — Actions logs are public.

    python3 -m agents.bhaga.scripts.clear_adp_weeks 2026-11-09 2026-11-29 --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import uuid

from agents.bhaga.scripts import adp_schedule_write as w
from core.datastore import fq
from skills.bhaga_labor.punch_gaps import parse_range, to_minutes

_MAX_DAYS = 35


def _spans(ranges_json: str | None) -> list[tuple[int, int, bool]]:
    """ADP's shift_ranges_json → [(start_min, end_min, draft)], unparseable ranges dropped (as the console does)."""
    try:
        ranges = json.loads(ranges_json or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for r in ranges if isinstance(ranges, list) else []:
        times = parse_range(r) if isinstance(r, str) else None
        if times and to_minutes(times[1]) > to_minutes(times[0]):
            out.append((to_minutes(times[0]), to_minutes(times[1]), "draft" in r.lower()))
    return out


def clear_rows(assigned: list[dict], open_slots: list[dict], *, limit: int = 0) -> tuple[list[dict], int]:
    """(change rows ready for queue_changes, published count). `limit` keeps only the first N assigned shifts."""
    shifts = sorted(
        (r["date"], s, e, r["employee"], draft)
        for r in assigned for s, e, draft in _spans(r["shift_ranges_json"])
    )
    opens = sorted(
        (r["date"], s, e) for r in open_slots for s, e, _ in _spans(json.dumps([r["shift_range"]]))
    )
    if limit:
        shifts, opens = shifts[:limit], []
    rows = [{"kind": "shift-delete", "date": d, "start_min": s, "end_min": e, "employee": emp} for d, s, e, emp, _ in shifts]
    rows += [{"kind": "open-delete", "date": d, "start_min": s, "end_min": e, "employee": None} for d, s, e in opens]
    for r in rows:
        r.update(to_start_min=None, to_end_min=None, to_employee=None, count=None)
    return rows, sum(1 for *_, draft in shifts if not draft)


def _summary(r: dict) -> str:
    when = f"{r['date'].strftime('%a %b')} {r['date'].day} · {w.wb.fmt_time(r['start_min'])}–{w.wb.fmt_time(r['end_min'])}"
    return f"{r['employee']} · {when}" if r["employee"] else f"Open shift · {when}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", default="palmetto")
    ap.add_argument("--by", default="agent:prod-ops")
    ap.add_argument("--limit", type=int, default=0, help="only the first N assigned shifts (canary)")
    ap.add_argument("--dry-run", action="store_true", help="print the counts; queue and start nothing")
    ap.add_argument("start", type=dt.date.fromisoformat)
    ap.add_argument("through", type=dt.date.fromisoformat)
    a = ap.parse_args(argv)
    today = w._today_ct()
    if a.start <= today or a.through < a.start or (a.through - a.start).days >= _MAX_DAYS or a.limit < 0:
        print(f"clear-weeks: refused — dates must run from after today ({today}) for at most {_MAX_DAYS} days")
        return 2
    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")
    params = [("store", "STRING", a.store), ("a", "DATE", a.start), ("b", "DATE", a.through)]
    assigned = w._query(
        f"""SELECT s.date, COALESCE(al.canonical_name, NULLIF(TRIM(s.employee_name), ''), s.employee_id) AS employee,
                   s.shift_ranges_json
            FROM {{T}} s LEFT JOIN {fq('employee_aliases')} al ON al.store = @store AND al.raw_name = TRIM(s.employee_name)
            WHERE s.date BETWEEN @a AND @b AND IFNULL(s.scheduled_hours, 0) > 0
              AND IFNULL(s.hour_kind, 'shift') != 'pto'""",
        params, table="adp_scheduled_shifts")
    open_slots = w._query(
        "SELECT date, shift_range FROM {T} WHERE date BETWEEN @a AND @b AND IFNULL(scheduled_hours, 0) > 0",
        params[1:], table="adp_open_shifts")
    rows, published = clear_rows(assigned, open_slots, limit=a.limit)
    opens = sum(1 for r in rows if r["kind"] == "open-delete")
    counts = f"shifts={len(rows) - opens} published={published} open={opens}"
    if not rows:
        print(f"clear-weeks: nothing in ADP to clear {a.start}..{a.through}")
        return 0
    if len(rows) > w._MAX_CHANGES:
        print(f"clear-weeks: refused — {len(rows)} deletes, one Apply takes {w._MAX_CHANGES}; clear fewer weeks")
        return 2
    if a.dry_run:
        print(f"clear-weeks: dry run {a.start}..{a.through} {counts} — nothing queued")
        return 0
    busy = w.running_schedule_write(w.fetch_job_executions())
    if busy:
        print(f"clear-weeks: refused — ADP is busy with another schedule write ({busy}); nothing queued")
        return 1
    for r in rows:
        r.update(change_id=str(uuid.uuid4()), requested_by=a.by, summary=_summary(r))
    batch_id = w.queue_changes(a.store, rows, by=a.by)
    print(f"clear-weeks: queued batch_id={batch_id} {a.start}..{a.through} {counts}")
    print(f"clear-weeks: started {w.start_apply_job(a.store, batch_id)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
