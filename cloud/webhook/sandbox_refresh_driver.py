#!/usr/bin/env python3
"""Evidence harness for the multi-date `/bhaga-cloud refresh` command.

Drives the real webhook handler's slash-command dispatch against the
``bhaga-sandbox-refresh`` Cloud Run job so the evidence proves the actual
parse → coverage-probe → trigger path, not a mock. Calls
``_handle_slash_command(form, sandbox=True)`` in-process, which routes to
the sandbox job + ``bhaga_sandbox`` BQ dataset via the module-level
``_SANDBOX_JOB_RESOURCE`` / ``_SANDBOX_BQ_DATASET`` constants (no env
mutation). All GCP access (run_v2 trigger, execution polling, BQ verify,
Firestore verify) uses ADC via the Python google-cloud libraries — no
``gcloud``/``bq`` CLI required.

Usage (operator, from a machine with ADC):

    # 1. Ensure the sandbox job exists + is sandbox-isolated
    #    (BHAGA_RUN_ENV=sandbox, BHAGA_OTP_ASSUME_READY=1).
    # 2. Run this driver:
    GCP_PROJECT=jarvis-bhaga-prod python3 cloud/webhook/sandbox_refresh_driver.py \\
        --dates 2026-06-23,2026-06-24 [--wait-minutes 30]

The sandbox job runs with BHAGA_OTP_ASSUME_READY=1, so full+OTP dates
service ADP inline — NO Slack OTP prompt to reply to. The driver polls
until both executions finish, then verifies BQ rows + Firestore and
prints a markdown evidence table (exit 0 = all dates PASS).

Env vars:
    GCP_PROJECT         — jarvis-bhaga-prod (or set GOOGLE_CLOUD_PROJECT)
    SLACK_SIGNING_SECRET — any non-empty string (handler import only)
    AGENT_CONFIG_JSON   — can be "{}" (not used for slash commands)
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# Minimal env so handler imports cleanly without real GCP creds at import time.
os.environ.setdefault("SLACK_SIGNING_SECRET", "sandbox-evidence-driver-no-verify")
os.environ.setdefault("AGENT_CONFIG_JSON", "{}")

_SANDBOX_JOB_NAME = os.environ.get(
    "BHAGA_SANDBOX_JOB_NAME",
    "projects/jarvis-bhaga-prod/locations/us-central1/jobs/bhaga-sandbox-refresh",
)
_SANDBOX_BQ_DATASET = os.environ.get("BHAGA_SANDBOX_BQ_DATASET", "bhaga_sandbox")
_SANDBOX_RUNS_COLLECTION = os.environ.get("BHAGA_SANDBOX_RUNS_COLLECTION", "sandbox_runs")
_BQ_PROJECT = os.environ.get("GCP_PROJECT", "jarvis-bhaga-prod")
_POLL_INTERVAL_S = 30
_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "EXECUTION_FAILED", "COMPLETED"}


def _inject_sandbox_env() -> None:
    """Set minimal env so handler imports cleanly.

    The sandbox job and BQ dataset are now controlled by passing
    sandbox=True to _handle_slash_command, which uses the module-level
    _SANDBOX_JOB_RESOURCE and _SANDBOX_BQ_DATASET constants rather than
    mutating env globals. We still set GCP_PROJECT so init_app() can
    initialize the BQ client correctly.
    """
    os.environ.setdefault("GCP_PROJECT", _BQ_PROJECT)


def _import_handler():
    """Import handler after injecting sandbox env (deferred to avoid import-time side effects)."""
    _inject_sandbox_env()
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    import cloud.webhook.handler as h
    # Re-initialize BQ and Firestore clients with the updated env.
    h.init_app()
    return h


def _fire_slash_command(handler, dates_text: str) -> dict:
    """Call _handle_slash_command with sandbox=True, exercising the bypass path.

    sandbox=True routes to _SANDBOX_JOB_RESOURCE + bhaga_sandbox BQ dataset
    and prefixes the ack with :test_tube: [SANDBOX].
    """
    form = {
        "text": f"refresh {dates_text}",
        "command": "/bhaga-cloud",
        "user_name": "sandbox-evidence-driver",
        "user_id": "U_DRIVER",
    }
    with handler.app.app_context():
        resp = handler._handle_slash_command(form, sandbox=True)
    data = resp.get_json()
    return data


def _list_executions(job_short_name: str, region: str = "us-central1",
                     project: str = _BQ_PROJECT) -> list:
    """Return recent executions for the sandbox job via run_v2 (ADC, no gcloud CLI)."""
    try:
        from google.cloud import run_v2
        client = run_v2.ExecutionsClient()
        job_resource = f"projects/{project}/locations/{region}/jobs/{job_short_name}"
        request = run_v2.ListExecutionsRequest(parent=job_resource, page_size=20)
        return list(client.list_executions(request=request))
    except Exception as exc:
        print(f"  WARN: executions list failed: {exc}", file=sys.stderr)
        return []


def _execution_state(execution) -> str:
    """Extract the terminal state string from a run_v2 Execution proto.

    The Execution proto exposes conditions (not succeeded_count/failed_count).
    The "Completed" condition with CONDITION_SUCCEEDED means the job succeeded;
    with CONDITION_FAILED it failed. While running the state is CONDITION_RECONCILING.
    """
    for cond in execution.conditions:
        if cond.type_ == "Completed":
            from google.cloud.run_v2.types.condition import Condition
            if cond.state == Condition.State.CONDITION_SUCCEEDED:
                return "SUCCEEDED"
            if cond.state == Condition.State.CONDITION_FAILED:
                return "FAILED"
    # completion_time present but no conclusive condition → treat as COMPLETED
    if execution.completion_time and execution.completion_time.seconds > 0:
        return "COMPLETED"
    return "RUNNING"


def _refresh_date_from_execution(execution) -> str | None:
    """Extract the REFRESH_DATE env value from a run_v2 Execution proto.

    The run-time env overrides are merged into execution.template.containers[i].env,
    so REFRESH_DATE is accessible there even though it was passed as a RunJobRequest
    override rather than baked into the job definition.
    """
    try:
        for container in execution.template.containers:
            for env_entry in container.env:
                if env_entry.name == "REFRESH_DATE":
                    return env_entry.value
    except Exception:
        pass
    return None


def _poll_executions_until_done(
    dates: list[str], job_short_name: str, wait_minutes: int
) -> dict[str, str]:
    """Poll via run_v2 until executions for all dates reach a terminal state.

    Returns {date: state} for each date. Dates with no matching execution
    after the timeout are reported as TIMEOUT.
    """
    deadline = time.time() + wait_minutes * 60
    states: dict[str, str] = {d: "PENDING" for d in dates}

    print(f"\nPolling executions for {dates} (timeout {wait_minutes} min)...")
    while time.time() < deadline:
        executions = _list_executions(job_short_name)
        for exe in executions:
            exe_date = _refresh_date_from_execution(exe)
            if exe_date and exe_date in states and states[exe_date] not in _TERMINAL_STATES:
                state = _execution_state(exe)
                if state != states[exe_date]:
                    states[exe_date] = state
                    print(f"  {exe_date}: {state}")

        not_found = [d for d, s in states.items() if s == "PENDING"]
        running = [d for d, s in states.items() if s not in _TERMINAL_STATES and s != "PENDING"]
        all_done = all(s in _TERMINAL_STATES for s in states.values())
        if all_done:
            break
        if not_found:
            print(f"  Waiting for executions to appear: {not_found}")
        if running:
            print(f"  Still running: {running}")
        print(f"  Sleeping {_POLL_INTERVAL_S}s ...")
        time.sleep(_POLL_INTERVAL_S)

    for d, s in states.items():
        if s not in _TERMINAL_STATES:
            states[d] = "TIMEOUT"
    return states


def _verify_bq(dates: list[str]) -> dict[str, int | None]:
    """Query bhaga_sandbox.square_item_lines for each date via the Python BQ client.

    Uses a parameterized query so date values are never interpolated directly
    into the SQL string.
    """
    counts: dict[str, int | None] = {}
    try:
        from google.cloud import bigquery
        bq = bigquery.Client(project=_BQ_PROJECT)
    except Exception as exc:
        print(f"  WARN: BQ client init failed: {exc}", file=sys.stderr)
        return {d: None for d in dates}

    sql = (
        f"SELECT COUNT(*) AS n"
        f" FROM `{_BQ_PROJECT}.{_SANDBOX_BQ_DATASET}.square_item_lines`"
        f" WHERE date_local = @date"
    )
    for date_str in dates:
        try:
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("date", "STRING", date_str)
                ]
            )
            rows = list(bq.query(sql, job_config=job_config).result())
            counts[date_str] = rows[0]["n"] if rows else None
        except Exception as exc:
            print(f"  WARN: BQ query failed for {date_str}: {exc}", file=sys.stderr)
            counts[date_str] = None
    return counts


def _verify_firestore(dates: list[str]) -> dict[str, str | None]:
    """Read sandbox_runs/<date> from Firestore; return {date: status}."""
    try:
        from google.cloud import firestore as _fs
        fsclient = _fs.Client(project=_BQ_PROJECT)
    except Exception as exc:
        print(f"  WARN: Firestore client init failed: {exc}", file=sys.stderr)
        return {d: None for d in dates}
    statuses: dict[str, str | None] = {}
    for date_str in dates:
        try:
            doc = fsclient.collection(_SANDBOX_RUNS_COLLECTION).document(date_str).get()
            if doc.exists:
                statuses[date_str] = (doc.to_dict() or {}).get("status")
            else:
                statuses[date_str] = None
        except Exception as exc:
            print(f"  WARN: Firestore read failed for {date_str}: {exc}", file=sys.stderr)
            statuses[date_str] = None
    return statuses


def _print_evidence_summary(
    *,
    dates: list[str],
    ack_text: str,
    exec_states: dict[str, str],
    bq_counts: dict[str, int | None],
    fs_statuses: dict[str, str | None],
) -> bool:
    """Print the markdown evidence summary and return True if all checks pass."""
    print("\n" + "=" * 60)
    print("### BHAGA multi-date refresh — sandbox evidence summary\n")
    print(f"**Command:** `/bhaga-cloud refresh {','.join(dates)}`")
    print(f"**Ack text:** {ack_text!r}\n")
    print("| Date | Execution | BQ rows (square_item_lines) | Firestore status |")
    print("|---|---|---|---|")
    all_pass = True
    for d in dates:
        exe_state = exec_states.get(d, "UNKNOWN")
        bq_n = bq_counts.get(d)
        fs_s = fs_statuses.get(d, "unknown")
        row_ok = bq_n is not None and bq_n > 0
        exe_ok = exe_state == "SUCCEEDED"
        if not row_ok or not exe_ok:
            all_pass = False
        bq_cell = f"{bq_n} rows" if bq_n is not None else "MISSING"
        print(f"| {d} | {exe_state} | {bq_cell} | {fs_s} |")
    print("\n" + ("PASS — all dates verified." if all_pass else "FAIL — see table above."))
    print("=" * 60 + "\n")
    return all_pass


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument(
        "--dates", required=True,
        help="Comma/range date spec to pass to the refresh command, e.g. '2026-06-23,2026-06-24'",
    )
    cli.add_argument(
        "--wait-minutes", type=int, default=60,
        help="How long to poll for execution completion (default 60 min).",
    )
    cli.add_argument(
        "--job-short-name", default="bhaga-sandbox-refresh",
        help="Short Cloud Run job name for gcloud polling (default: bhaga-sandbox-refresh).",
    )
    cli.add_argument(
        "--no-wait", action="store_true",
        help="Fire the command and print the ack, then exit without polling.",
    )
    args = cli.parse_args(argv)

    print("[sandbox_refresh_driver] Importing handler with sandbox env overrides ...")
    handler = _import_handler()

    print(f"[sandbox_refresh_driver] Firing: /bhaga-cloud refresh {args.dates}")
    ack = _fire_slash_command(handler, args.dates)
    ack_text = ack.get("text", "")
    print(f"[sandbox_refresh_driver] Ack: {ack_text!r}")

    if ":x:" in ack_text:
        print("[sandbox_refresh_driver] FAIL — command returned an error.", file=sys.stderr)
        return 1

    # Resolve the date list from the ack (or re-parse locally).
    from cloud.webhook.handler import _parse_refresh_dates
    dates, parse_err = _parse_refresh_dates(args.dates)
    if parse_err:
        print(f"[sandbox_refresh_driver] FAIL — date parse error: {parse_err}", file=sys.stderr)
        return 1
    print(f"[sandbox_refresh_driver] Resolved dates: {dates}")
    print("[sandbox_refresh_driver] Reply to ADP OTP prompts in Slack (labeled [SANDBOX ·…]).")

    if args.no_wait:
        print("[sandbox_refresh_driver] --no-wait: skipping execution polling.")
        return 0

    exec_states = _poll_executions_until_done(dates, args.job_short_name, args.wait_minutes)
    print(f"[sandbox_refresh_driver] Final execution states: {exec_states}")

    print("[sandbox_refresh_driver] Verifying BQ ...")
    bq_counts = _verify_bq(dates)

    print("[sandbox_refresh_driver] Verifying Firestore ...")
    fs_statuses = _verify_firestore(dates)

    all_pass = _print_evidence_summary(
        dates=dates,
        ack_text=ack_text,
        exec_states=exec_states,
        bq_counts=bq_counts,
        fs_statuses=fs_statuses,
    )
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
