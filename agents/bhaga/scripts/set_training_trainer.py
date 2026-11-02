#!/usr/bin/env python3
"""Set one person as a per-kind new-hire trainer (Issue #450 / PR #484 follow-up).

Appends a `labor_schedule_rules` version that: adds them to `staffing.training.experienced`,
removes them from `trained`, and sets `trainsOnly` to open / close (or clears it for both).
Same first-name resolution and counts-only output as `set_experienced_staff.py`.

    python3 -m agents.bhaga.scripts.set_training_trainer --store palmetto Wing closers-only
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from agents.bhaga.scripts.set_experienced_staff import DEFAULT_TRAINING, resolve
from core.datastore import _param_config, fq, get_client

KINDS = {
    "openers-only": "open",
    "closers-only": "close",
    "both": None,
}


def with_trainer(rules_json: str, employee: str, kind: str | None) -> dict:
    """Add `employee` as experienced; drop them from trained; set or clear trainsOnly."""
    rules = json.loads(rules_json)
    staffing = rules.get("staffing")
    if not isinstance(staffing, dict):
        raise ValueError("live rules have no staffing section — save Staffing basics in the console once first")
    training = {**DEFAULT_TRAINING, **(staffing.get("training") or {})}
    experienced = list(training.get("experienced") or [])
    if employee not in experienced:
        experienced.append(employee)
    training["experienced"] = sorted(experienced)
    training["trained"] = [n for n in (training.get("trained") or []) if n != employee]
    trains_only = dict(training.get("trainsOnly") or {})
    trained_only = dict(training.get("trainedOnly") or {})
    trains_only.pop(employee, None)
    trained_only.pop(employee, None)
    if kind:
        trains_only[employee] = kind
    training["trainsOnly"] = trains_only
    training["trainedOnly"] = trained_only
    staffing["training"] = training
    return rules


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", default="palmetto")
    ap.add_argument("--by", default="prod-ops")
    ap.add_argument("first_name")
    ap.add_argument("kind", choices=sorted(KINDS))
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
    names, bad = resolve([a.first_name], roster)
    if bad:
        print(f"training-trainer: refused — need exactly one Active match for the first name, got {bad}")
        return 2
    employee = names[0]
    live = list(client.query(
        f"SELECT version, rules_json FROM {fq('labor_schedule_rules')} WHERE store = @store "
        "ORDER BY version DESC LIMIT 1", job_config=params).result())
    if not live:
        print("training-trainer: refused — no saved rules yet; save Scheduling rules in the console once first")
        return 2
    version = int(live[0].version)
    try:
        rules = with_trainer(live[0].rules_json, employee, KINDS[a.kind])
    except ValueError as e:
        print(f"training-trainer: refused — {e}")
        return 2
    kind_label = a.kind
    job = client.query(
        f"""INSERT INTO {fq('labor_schedule_rules')} (store, version, rules_json, note, created_by, created_at)
            SELECT @store, @next, @rules, @note, @by, CURRENT_TIMESTAMP()
            FROM (SELECT 1) WHERE NOT EXISTS (
              SELECT 1 FROM {fq('labor_schedule_rules')} WHERE store = @store AND version >= @next)""",
        job_config=_param_config([
            ("store", "STRING", a.store), ("next", "INT64", version + 1),
            ("rules", "STRING", json.dumps(rules)), ("by", "STRING", a.by),
            ("note", "STRING", f"New-hire training: trainer {kind_label} (/prod-ops)"),
        ]),
    )
    job.result()
    if job.num_dml_affected_rows != 1:
        print(f"training-trainer: refused — rules changed past version {version} while saving; rerun")
        return 2
    print(f"training-trainer: saved store={a.store} version {version} → {version + 1} kind={kind_label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
