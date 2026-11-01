#!/usr/bin/env python3
"""Save the new-hire training "experienced staff" list as a new scheduling-rules version (Issue #450).

The same append the console's Labor → Scheduling rules → Save does: read the live
`labor_schedule_rules` version, replace `staffing.training.experienced`, insert version + 1
(earlier versions stay as history). Run by `/prod-ops experienced-staff <First> …`; people are
named by first name and resolved against the ADP Directory so no full names land in the public
comment. Output carries counts only — Actions logs are public.

    python3 -m agents.bhaga.scripts.set_experienced_staff --store palmetto Ana Ben
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from core.datastore import _param_config, fq, get_client

DEFAULT_TRAINING = {"openShifts": 2, "closeShifts": 2, "overlapMin": 180}


def first_name(canonical: str) -> str:
    """'Last, First Middle' → 'first'; a name without a comma is its own first token."""
    _, _, rest = canonical.partition(",")
    tokens = (rest or canonical).split()
    return tokens[0].lower() if tokens else ""


def resolve(firsts: list[str], roster: list[str]) -> tuple[list[str], dict[str, int]]:
    """(canonical names, {first: match count} for every first name without exactly one match)."""
    found, bad = [], {}
    for f in firsts:
        hits = sorted({n for n in roster if first_name(n) == f.lower()})
        if len(hits) == 1:
            found.append(hits[0])
        else:
            bad[f] = len(hits)
    return sorted(set(found)), bad


def with_experienced(rules_json: str, names: list[str]) -> dict:
    """The rules with `staffing.training.experienced` replaced; other training settings kept."""
    rules = json.loads(rules_json)
    staffing = rules.get("staffing")
    if not isinstance(staffing, dict):
        raise ValueError("live rules have no staffing section — save Staffing basics in the console once first")
    staffing["training"] = {**DEFAULT_TRAINING, **(staffing.get("training") or {}), "experienced": names}
    return rules


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", default="palmetto")
    ap.add_argument("--by", default="prod-ops")
    ap.add_argument("first_names", nargs="+")
    a = ap.parse_args(argv)
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
    names, bad = resolve(a.first_names, roster)
    if bad:
        print(f"experienced-staff: refused — need exactly one Active match per first name, got {bad}")
        return 2
    live = list(client.query(
        f"SELECT version, rules_json FROM {fq('labor_schedule_rules')} WHERE store = @store "
        "ORDER BY version DESC LIMIT 1", job_config=params).result())
    if not live:
        print("experienced-staff: refused — no saved rules yet; save Scheduling rules in the console once first")
        return 2
    version = int(live[0].version)
    try:
        rules = with_experienced(live[0].rules_json, names)
    except ValueError as e:
        print(f"experienced-staff: refused — {e}")
        return 2
    job = client.query(
        f"""INSERT INTO {fq('labor_schedule_rules')} (store, version, rules_json, note, created_by, created_at)
            SELECT @store, @next, @rules, @note, @by, CURRENT_TIMESTAMP()
            FROM (SELECT 1) WHERE NOT EXISTS (
              SELECT 1 FROM {fq('labor_schedule_rules')} WHERE store = @store AND version >= @next)""",
        job_config=_param_config([
            ("store", "STRING", a.store), ("next", "INT64", version + 1),
            ("rules", "STRING", json.dumps(rules)), ("by", "STRING", a.by),
            ("note", "STRING", f"New-hire training: {len(names)} experienced staff (/prod-ops)"),
        ]),
    )
    job.result()
    if job.num_dml_affected_rows != 1:
        print(f"experienced-staff: refused — rules changed past version {version} while saving; rerun")
        return 2
    print(f"experienced-staff: saved store={a.store} version {version} → {version + 1} experienced={len(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
