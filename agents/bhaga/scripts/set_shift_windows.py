#!/usr/bin/env python3
"""Save one person's "Shifts must fit within" staff rule as a new scheduling-rules version.

The same append the console's Labor → Scheduling rules → Save does: read the live
`labor_schedule_rules` version, replace that person's `shift_windows` rules with one rule,
insert version + 1 (earlier versions stay as history). Run by
`/prod-ops shift-windows <First> HH:MM-HH:MM … [days=Mon,Tue,…]`; the person is named by first
name and resolved against the ADP Directory so no full name lands in the public comment.
Output carries counts only — Actions logs are public.

    python3 -m agents.bhaga.scripts.set_shift_windows --store palmetto Ana 09:00-17:00 16:00-20:30
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from agents.bhaga.scripts.set_experienced_staff import resolve
from core.datastore import _param_config, fq, get_client

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_WINDOW = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")


def parse_window(text: str) -> dict:
    """'09:00-17:00' → {fromMin, toMin}; raises ValueError on a bad or backwards window."""
    m = _WINDOW.match(text)
    if not m:
        raise ValueError(f"window {text!r} must be HH:MM-HH:MM")
    h1, m1, h2, m2 = map(int, m.groups())
    lo, hi = h1 * 60 + m1, h2 * 60 + m2
    if m1 > 59 or m2 > 59 or not 0 <= lo < hi <= 24 * 60:
        raise ValueError(f"window {text!r} needs a from time before the to time")
    return {"fromMin": lo, "toMin": hi}


def parse_days(text: str | None) -> list[int]:
    """'Mon,Wed' → [0, 2]; None = every day."""
    if not text:
        return list(range(7))
    try:
        days = sorted({DAY_NAMES.index(d.strip().lower()[:3]) for d in text.split(",") if d.strip()})
    except ValueError:
        raise ValueError(f"days {text!r} must be weekday names like Mon,Tue") from None
    if not days:
        raise ValueError("days must name at least one weekday")
    return days


def with_shift_windows(rules_json: str, employee: str, windows: list[dict], days: list[int]) -> dict:
    """The rules with this person's shift_windows rules replaced by one rule; everything else kept."""
    rules = json.loads(rules_json)
    staff = [r for r in rules.get("staffRules", []) if not (r.get("employee") == employee and r.get("kind") == "shift_windows")]
    staff.append({"id": f"shift-windows-{int(time.time() * 1000)}", "employee": employee, "kind": "shift_windows", "value": 0,
                  "days": days, "windows": sorted(windows, key=lambda w: w["fromMin"])})
    rules["staffRules"] = staff
    return rules


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", default="palmetto")
    ap.add_argument("--by", default="prod-ops")
    ap.add_argument("--days", help="weekdays, e.g. Mon,Tue (default every day)")
    ap.add_argument("first_name")
    ap.add_argument("windows", nargs="+", help="HH:MM-HH:MM, 1-4 of them")
    a = ap.parse_args(argv)
    try:
        windows = [parse_window(w) for w in a.windows]
        days = parse_days(a.days)
        if len(windows) > 4:
            raise ValueError("at most 4 windows")
    except ValueError as e:
        print(f"shift-windows: refused — {e}")
        return 2
    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")
    client = get_client()
    params = _param_config([("store", "STRING", a.store)])
    roster = [r.employee for r in client.query(
        f"""WITH snap AS (
              SELECT employee_name, employment_status FROM {fq('adp_directory_status')}
              WHERE store = @store QUALIFY scraped_at_utc = MAX(scraped_at_utc) OVER ())
            SELECT DISTINCT COALESCE(al.canonical_name, s.employee_name) AS employee
            FROM snap s LEFT JOIN {fq('employee_aliases')} al
              ON al.store = @store AND al.raw_name = s.employee_name
            WHERE s.employment_status = 'Active'""", job_config=params).result()]
    names, bad = resolve([a.first_name], roster)
    if bad:
        print(f"shift-windows: refused — need exactly one Active match for the first name, got {bad}")
        return 2
    live = list(client.query(
        f"SELECT version, rules_json FROM {fq('labor_schedule_rules')} WHERE store = @store "
        "ORDER BY version DESC LIMIT 1", job_config=params).result())
    if not live:
        print("shift-windows: refused — no saved rules yet; save Scheduling rules in the console once first")
        return 2
    version = int(live[0].version)
    rules = with_shift_windows(live[0].rules_json, names[0], windows, days)
    job = client.query(
        f"""INSERT INTO {fq('labor_schedule_rules')} (store, version, rules_json, note, created_by, created_at)
            SELECT @store, @next, @rules, @note, @by, CURRENT_TIMESTAMP()
            FROM (SELECT 1) WHERE NOT EXISTS (
              SELECT 1 FROM {fq('labor_schedule_rules')} WHERE store = @store AND version >= @next)""",
        job_config=_param_config([
            ("store", "STRING", a.store), ("next", "INT64", version + 1),
            ("rules", "STRING", json.dumps(rules)), ("by", "STRING", a.by),
            ("note", "STRING", f"Shift windows: 1 person, {len(windows)} windows, {len(days)} days (/prod-ops)"),
        ]),
    )
    job.result()
    if job.num_dml_affected_rows != 1:
        print(f"shift-windows: refused — rules changed past version {version} while saving; rerun")
        return 2
    print(f"shift-windows: saved store={a.store} version {version} → {version + 1} windows={len(windows)} days={len(days)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
