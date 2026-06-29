#!/usr/bin/env python3
"""One-shot Cloud Scheduler smart retry for BHAGA's ADP maintenance skips.

When a nightly run is skipped because ADP redirected to sorry.adp.com during a
scheduled RUN maintenance window (see skills/adp_run_automation/maintenance.py),
daily_refresh schedules a single retry shortly after the window closes — instead
of waiting ~24h for the next nightly.

Mechanism (operator decision 2026-06-29, option A): create an ephemeral Cloud
Scheduler job ``bhaga-retry-<date>`` that mirrors the production ``bhaga-nightly``
trigger exactly — an HTTP target to the Cloud Run Job ``:run`` endpoint, OAuth as
the ``bhaga-orchestrator`` service account — but fires once at the retry time and
carries a ``REFRESH_DATE`` override. The scheduler is removed at the start of the
run it triggers (``delete_retry_schedule``), so it is self-cleaning.

The Cloud Scheduler client is injected (``client=``) so the build/cron logic is
unit-testable without touching GCP. Building the job spec (``build_retry_job`` /
``cron_for``) is pure.
"""

from __future__ import annotations

import datetime
import json
import os
from zoneinfo import ZoneInfo

PROJECT = os.environ.get("GCP_PROJECT", "jarvis-bhaga-prod")
REGION = os.environ.get("BHAGA_REGION", "us-central1")


def _job_short() -> str:
    """The Cloud Run Job the retry should target — the one CURRENTLY executing.

    Cloud Run injects ``CLOUD_RUN_JOB`` into every job execution, so a sandbox run
    (``bhaga-sandbox-refresh``) schedules a retry against the SANDBOX job and a
    prod run against ``bhaga-daily-refresh`` — sandbox never triggers prod. Falls
    back to the explicit override / prod default outside Cloud Run (e.g. tests).
    """
    return (
        os.environ.get("CLOUD_RUN_JOB")
        or os.environ.get("CLOUD_RUN_JOB_NAME_SHORT")
        or "bhaga-daily-refresh"
    )
# The SA the scheduler authenticates as when calling the Run :run endpoint —
# the same SA bhaga-nightly uses (already has run.invoker on the job).
INVOKER_SA = os.environ.get(
    "BHAGA_RETRY_INVOKER_SA", "bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com"
)
SCHEDULER_TZ = "America/Chicago"
MAX_MAINTENANCE_RETRIES = int(os.environ.get("BHAGA_MAINT_RETRY_MAX", "3"))

_OAUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def parent_path() -> str:
    return f"projects/{PROJECT}/locations/{REGION}"


def job_path(date_iso: str) -> str:
    return f"{parent_path()}/jobs/bhaga-retry-{date_iso}"


def _run_uri() -> str:
    # v1 "namespaces" Run API — identical shape to the bhaga-nightly target.
    return (
        f"https://{REGION}-run.googleapis.com/apis/run.googleapis.com/v1/"
        f"namespaces/{PROJECT}/jobs/{_job_short()}:run"
    )


def cron_for(dt_local: datetime.datetime) -> str:
    """One-shot-style cron 'M H D Mo *' for a local wall-clock datetime.

    Cloud Scheduler has no native one-shot; this fires at the given minute on the
    given calendar day. The triggered run deletes the schedule, so it never
    repeats in practice (and would otherwise only re-fire a year later).
    """
    return f"{dt_local.minute} {dt_local.hour} {dt_local.day} {dt_local.month} *"


def build_retry_job(
    date_iso: str,
    retry_at_utc: datetime.datetime,
    *,
    env: dict[str, str] | None = None,
    tz: str = SCHEDULER_TZ,
) -> dict:
    """Pure: build the Cloud Scheduler Job spec (dict) for a one-shot retry."""
    local = retry_at_utc.astimezone(ZoneInfo(tz))
    env = env or {}
    body = {
        "overrides": {
            "containerOverrides": [
                {"env": [{"name": k, "value": v} for k, v in env.items()]}
            ]
        }
    }
    return {
        "name": job_path(date_iso),
        "description": (
            f"BHAGA smart retry for {date_iso} after ADP maintenance window "
            f"(fires {local.isoformat()} {tz}). Auto-deleted by the run it triggers."
        ),
        "schedule": cron_for(local),
        "time_zone": tz,
        "http_target": {
            "uri": _run_uri(),
            "http_method": "POST",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body).encode("utf-8"),
            "oauth_token": {
                "service_account_email": INVOKER_SA,
                "scope": _OAUTH_SCOPE,
            },
        },
    }


def _client(client=None):
    if client is not None:
        return client
    from google.cloud import scheduler_v1  # lazy: keeps import optional for tests
    return scheduler_v1.CloudSchedulerClient()


def schedule_one_shot_retry(
    date_iso: str,
    retry_at_utc: datetime.datetime,
    *,
    env: dict[str, str] | None = None,
    client=None,
    tz: str = SCHEDULER_TZ,
):
    """Create (idempotently) the one-shot retry scheduler. Returns the created job.

    Delete-then-create so a re-scheduled retry (e.g. the window slipped) replaces
    the prior one rather than erroring on AlreadyExists.
    """
    cl = _client(client)
    job = build_retry_job(date_iso, retry_at_utc, env=env, tz=tz)
    try:
        cl.delete_job(name=job["name"])
    except Exception:  # noqa: BLE001 — not-found is the normal case
        pass
    return cl.create_job(parent=parent_path(), job=job)


def delete_retry_schedule(date_iso: str, *, client=None) -> bool:
    """Best-effort delete of ``bhaga-retry-<date>``. Returns True if a delete was issued.

    Called at the start of a dated run so the scheduler that triggered it (or any
    stale one for that date) is cleaned up. Never raises.
    """
    try:
        cl = _client(client)
        cl.delete_job(name=job_path(date_iso))
        return True
    except Exception:  # noqa: BLE001
        return False
