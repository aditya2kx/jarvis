#!/usr/bin/env python3
"""Record a deploy event to BigQuery and post a Grafana annotation.

Creates the ``jarvis_dev.deploys`` table on first run (idempotent). Posts an
annotation to the Jarvis Development Grafana dashboard so deploy lines appear
on all time-series panels. Annotation failure is a warning, not a job failure.

Usage (in deploy.yml, after WIF auth):
    for unit in orchestrator webhook; do
      python3 scripts/deploy_events.py record \\
        --agent bhaga --unit "$unit" \\
        --sha "${{ github.sha }}" \\
        --kind deploy \\
        --actor "${{ github.actor }}" \\
        --run-url "..."
    done

Auth: Application Default Credentials (ADC) for BigQuery; GRAFANA_API_TOKEN
env var for Grafana annotations.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

PROJECT_ID = "jarvis-bhaga-prod"
DATASET = os.environ.get("JARVIS_DEV_BQ_DATASET", "jarvis_dev")
TABLE = "deploys"
DASHBOARD_UID = "jarvis-dev-cost-v1"


class DeployEventsError(RuntimeError):
    pass


def _client():
    from google.cloud import bigquery
    try:
        return bigquery.Client(project=PROJECT_ID)
    except Exception:
        pass
    import subprocess
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token", f"--project={PROJECT_ID}"],
            text=True, stderr=subprocess.DEVNULL, timeout=15,
        ).strip()
    except Exception as exc:
        raise DeployEventsError(
            "No BigQuery credentials. Run `gcloud auth application-default login` "
            "locally, or ensure WIF auth in CI."
        ) from exc
    from google.oauth2.credentials import Credentials
    from google.cloud import bigquery as bq2  # noqa: F811
    return bq2.Client(project=PROJECT_ID, credentials=Credentials(token=token))


def _fq(table: str) -> str:
    return f"`{PROJECT_ID}.{DATASET}.{table}`"


def ensure_schema() -> None:
    """Idempotent: create dataset + deploys table if they do not exist."""
    from google.cloud import bigquery
    c = _client()
    ds = bigquery.Dataset(f"{PROJECT_ID}.{DATASET}")
    ds.location = "US"
    c.create_dataset(ds, exists_ok=True)
    ddl = f"""CREATE TABLE IF NOT EXISTS {_fq(TABLE)} (
        ts           TIMESTAMP NOT NULL,
        agent        STRING,
        unit         STRING,
        git_sha      STRING,
        image_tag    STRING,
        kind         STRING,
        actor        STRING,
        workflow_run_url STRING
    )"""
    c.query(ddl).result()
    print(f"[deploy_events] schema ready: {PROJECT_ID}.{DATASET}.{TABLE}")


def insert_row(
    *,
    agent: str,
    unit: str,
    git_sha: str,
    image_tag: str = "",
    kind: str = "deploy",
    actor: str = "",
    workflow_run_url: str = "",
) -> None:
    """Insert a single deploy event row into BigQuery."""
    c = _client()
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows = [{
        "ts": ts,
        "agent": agent,
        "unit": unit,
        "git_sha": git_sha,
        "image_tag": image_tag or git_sha[:12],
        "kind": kind,
        "actor": actor,
        "workflow_run_url": workflow_run_url,
    }]
    errors = c.insert_rows_json(f"{PROJECT_ID}.{DATASET}.{TABLE}", rows)
    if errors:
        raise DeployEventsError(f"BigQuery insert_rows_json errors: {errors}")
    print(f"[deploy_events] recorded {kind} for {agent}/{unit} sha={git_sha[:12]}")


def post_grafana_annotation(
    *,
    agent: str,
    unit: str,
    git_sha: str,
    kind: str = "deploy",
    actor: str = "",
) -> None:
    """POST a Grafana annotation to the Jarvis Development dashboard.

    Failure is logged as a warning and does not raise.
    """
    token = os.environ.get("GRAFANA_API_TOKEN", "").strip()
    org_slug = os.environ.get("GRAFANA_ORG_SLUG", "steadyangelfish2985").strip()
    if not token:
        print("[deploy_events] WARN: GRAFANA_API_TOKEN not set — skipping annotation")
        return

    now_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    sha7 = git_sha[:7]
    body = {
        "dashboardUID": DASHBOARD_UID,
        "time": now_ms,
        "tags": ["deploy", unit, agent, kind],
        "text": f"{kind} {unit} {sha7} by {actor or 'ci'}",
    }
    url = f"https://{org_slug}.grafana.net/api/annotations"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            print(f"[deploy_events] Grafana annotation id={result.get('id')} posted")
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"[deploy_events] WARN: Grafana annotation failed (non-fatal): {exc}")


def record(
    *,
    agent: str,
    unit: str,
    sha: str,
    image_tag: str = "",
    kind: str = "deploy",
    actor: str = "",
    run_url: str = "",
) -> None:
    """Full record: ensure schema, insert BQ row, post Grafana annotation."""
    ensure_schema()
    insert_row(
        agent=agent,
        unit=unit,
        git_sha=sha,
        image_tag=image_tag,
        kind=kind,
        actor=actor,
        workflow_run_url=run_url,
    )
    post_grafana_annotation(agent=agent, unit=unit, git_sha=sha, kind=kind, actor=actor)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Record a deploy event to BQ + Grafana")
    sub = cli.add_subparsers(dest="cmd")

    p_record = sub.add_parser("record", help="Record one deploy event")
    p_record.add_argument("--agent", required=True)
    p_record.add_argument("--unit", required=True)
    p_record.add_argument("--sha", required=True)
    p_record.add_argument("--image-tag", default="")
    p_record.add_argument("--kind", default="deploy", choices=["deploy", "rollback", "backfill"])
    p_record.add_argument("--actor", default="")
    p_record.add_argument("--run-url", default="")

    p_schema = sub.add_parser("ensure-schema", help="Create table if not exists (idempotent)")

    args = cli.parse_args()
    if args.cmd == "record":
        record(
            agent=args.agent,
            unit=args.unit,
            sha=args.sha,
            image_tag=args.image_tag,
            kind=args.kind,
            actor=args.actor,
            run_url=args.run_url,
        )
    elif args.cmd == "ensure-schema":
        ensure_schema()
    else:
        cli.print_help()
        sys.exit(1)
