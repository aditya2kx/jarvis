#!/usr/bin/env python3
"""Permanent Cloud Scheduler for BHAGA ADP payroll Start→Preview (leave draft).

``bhaga-nightly`` stays at 21:30 CT and never Starts payroll. This job
fires every Monday 07:00 America/Chicago against ``bhaga-daily-refresh:run``
with ``BHAGA_PAYROLL_DRAFT_ONLY=1``. Python no-ops when today is not the
day after a closed biweek Sunday (e.g. mid-biweek Mondays).

Never Approve. Spec build is pure; Cloud Scheduler client is injectable.
"""

from __future__ import annotations

import json
import os

from agents.bhaga.scripts import retry_scheduler as rs

JOB_ID = "bhaga-payroll-draft"
SCHEDULE = "0 7 * * 1"  # Monday 07:00
CLOUD_RUN_JOB = "bhaga-daily-refresh"


def job_path() -> str:
    return f"{rs.parent_path()}/jobs/{JOB_ID}"


def _run_uri() -> str:
    return (
        f"https://{rs.REGION}-run.googleapis.com/apis/run.googleapis.com/v1/"
        f"namespaces/{rs.PROJECT}/jobs/{CLOUD_RUN_JOB}:run"
    )


def payroll_draft_env(store: str = "palmetto") -> list[dict[str, str]]:
    return [
        {"name": "BHAGA_PAYROLL_DRAFT_ONLY", "value": "1"},
        {"name": "BHAGA_ADP_PAYROLL_DRAFT", "value": "1"},
        {"name": "BHAGA_IGNORE_HALT", "value": "1"},
        {"name": "BHAGA_SKIP_SQUARE", "value": "1"},
        {"name": "BHAGA_SKIP_KDS", "value": "1"},
        {"name": "BHAGA_STORE", "value": store},
    ]


def build_payroll_draft_job(*, store: str = "palmetto") -> dict:
    """Pure: Cloud Scheduler Job spec for Monday 07:00 CT payroll draft."""
    body = {"overrides": {"containerOverrides": [{"env": payroll_draft_env(store)}]}}
    return {
        "name": job_path(),
        "description": (
            "Monday 07:00 America/Chicago ADP payroll Start→Preview; leave draft. "
            "Never Approve. Python no-ops mid-biweek Mondays."
        ),
        "schedule": SCHEDULE,
        "time_zone": rs.SCHEDULER_TZ,
        "http_target": {
            "uri": _run_uri(),
            "http_method": "POST",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body).encode("utf-8"),
            "oauth_token": {
                "service_account_email": rs.INVOKER_SA,
                "scope": "https://www.googleapis.com/auth/cloud-platform",
            },
        },
    }


def _client(client=None):
    if client is not None:
        return client
    from google.cloud import scheduler_v1  # noqa: PLC0415

    return scheduler_v1.CloudSchedulerClient()


def upsert_payroll_draft_schedule(*, client=None, store: str = "palmetto") -> str:
    """Create or replace ``bhaga-payroll-draft``. Returns the job name."""
    spec = build_payroll_draft_job(store=store)
    c = _client(client)
    name = spec["name"]
    try:
        c.delete_job(name=name)
    except Exception:  # noqa: BLE001
        pass
    parent = rs.parent_path()
    c.create_job(parent=parent, job=spec)
    return name


if __name__ == "__main__":
    store = os.environ.get("BHAGA_STORE", "palmetto")
    print(upsert_payroll_draft_schedule(store=store))
