#!/usr/bin/env python3
"""GCP from a Cursor Cloud Agent VM with no static credential.

    python3 scripts/cloud_agent_gcp.py secret github-bot-pat     # secret value on stdout
    python3 scripts/cloud_agent_gcp.py id-token                  # IAP ID token (console previews)
    python3 scripts/cloud_agent_gcp.py bq "SELECT ..."           # prod BigQuery, JSON rows
    python3 scripts/cloud_agent_gcp.py logs --job bhaga-daily-refresh --since 1d --grep authfail
    python3 scripts/cloud_agent_gcp.py logs --filter 'severity>=ERROR' --since 6h
    python3 scripts/cloud_agent_gcp.py gcs-get gs://bhaga-scrape-cache/<obj> /tmp/x.png
    python3 scripts/cloud_agent_gcp.py run jobs/bhaga-daily-refresh/executions   # Cloud Run v2 GET

Cursor OIDC token (local agent socket) → GCP STS federated token → service account
cursor-cloud-agent (access token / ID token). Trust and the read-only prod roles are set up once by
scripts/provision_cloud_agent_identity.sh (docs/contributing/gcp-access.md recipe F). Prod changes
stay on `/prod-ops`.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import http.client
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

PROJECT = "jarvis-bhaga-prod"
REGION = "us-central1"
AGENT_SA = f"cursor-cloud-agent@{PROJECT}.iam.gserviceaccount.com"
AUDIENCE = (
    "//iam.googleapis.com/projects/887772634501/locations/global/"
    "workloadIdentityPools/cursor-agents/providers/cursor"
)
# OAuth client behind the Operator Console's IAP (prod and previews).
IAP_CLIENT_ID = "887772634501-rtccq8d5foru6kgcqbi3a1n6kmcqn2eu.apps.googleusercontent.com"
SOCKET = os.environ.get("CURSOR_AGENT_SOCKET") or "/run/cursor/api.sock"
IAM_CREDENTIALS = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{AGENT_SA}"


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__("localhost", timeout=10)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(self._path)


def cursor_oidc_token(aud: str = AUDIENCE, sock: str = SOCKET) -> str:
    conn = _UnixHTTPConnection(sock)
    conn.request("POST", "/v1/tokens/oidc", json.dumps({"aud": aud}), {"Content-Type": "application/json"})
    body = json.loads(conn.getresponse().read().decode() or "{}")
    if not body.get("token"):
        raise RuntimeError(f"Cursor OIDC: {body.get('error') or 'no token'}")
    return body["token"]


def _request(url: str, *, data: bytes | None = None, token: str | None = None, form: bool = False,
             raw: bool = False, timeout: int = 20):
    headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return body if raw else json.loads(body.decode())


def federated_access_token(oidc: str) -> str:
    return _request("https://sts.googleapis.com/v1/token", form=True, data=urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "audience": AUDIENCE,
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "subject_token": oidc,
    }).encode())["access_token"]


def sa_access_token(federated: str) -> str:
    body = json.dumps({"scope": ["https://www.googleapis.com/auth/cloud-platform"]}).encode()
    return _request(f"{IAM_CREDENTIALS}:generateAccessToken", data=body, token=federated)["accessToken"]


def sa_id_token(federated: str, audience: str = IAP_CLIENT_ID) -> str:
    body = json.dumps({"audience": audience, "includeEmail": True}).encode()
    return _request(f"{IAM_CREDENTIALS}:generateIdToken", data=body, token=federated)["token"]


def access_secret(name: str, token: str, project: str = PROJECT) -> str:
    url = f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{name}/versions/latest:access"
    return base64.b64decode(_request(url, token=token)["payload"]["data"]).decode()


def _bq_value(field: dict, cell):
    v = cell.get("v") if isinstance(cell, dict) else cell
    if v is None:
        return None
    if field.get("mode") == "REPEATED":
        return [_bq_value({**field, "mode": "NULLABLE"}, c) for c in v]
    if field["type"] in ("RECORD", "STRUCT"):
        return {f["name"]: _bq_value(f, c) for f, c in zip(field["fields"], v["f"])}
    if field["type"] in ("INTEGER", "INT64"):
        return int(v)
    if field["type"] in ("FLOAT", "FLOAT64"):
        return float(v)
    if field["type"] in ("BOOLEAN", "BOOL"):
        return v == "true"
    if field["type"] == "TIMESTAMP":
        return datetime.datetime.fromtimestamp(float(v), datetime.timezone.utc).isoformat()
    return v


def bq_query(sql: str, token: str, *, max_rows: int = 1000, project: str = PROJECT) -> list[dict]:
    """Rows as dicts. Read access only: the SA holds dataViewer + jobUser, nothing that writes."""
    base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}"
    res = _request(f"{base}/queries", token=token, timeout=120, data=json.dumps({
        "query": sql, "useLegacySql": False, "timeoutMs": 90_000, "maxResults": max_rows,
    }).encode())
    job = res["jobReference"]
    while not res.get("jobComplete"):
        res = _request(f"{base}/queries/{job['jobId']}?location={job.get('location', 'US')}"
                       f"&timeoutMs=90000&maxResults={max_rows}", token=token, timeout=120)
    fields = res["schema"]["fields"]
    return [{f["name"]: _bq_value(f, c) for f, c in zip(fields, row["f"])} for row in res.get("rows", [])]


_SINCE_RE = re.compile(r"^(\d+)([mhd])$")


def _since_ts(since: str, now: datetime.datetime | None = None) -> str:
    m = _SINCE_RE.match(since)
    if not m:
        raise ValueError(f"--since must look like 30m / 6h / 3d, got {since!r}")
    delta = datetime.timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[m[2]]: int(m[1])})
    return ((now or datetime.datetime.now(datetime.timezone.utc)) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def logs_filter(*, job: str | None, service: str | None, since: str, grep: str | None,
                extra: str | None, now: datetime.datetime | None = None) -> str:
    parts = [f'timestamp>="{_since_ts(since, now)}"']
    if job:
        parts.append(f'resource.type="cloud_run_job" resource.labels.job_name="{job}"')
    if service:
        parts.append(f'resource.type="cloud_run_revision" resource.labels.service_name="{service}"')
    if grep:
        parts.append(f'(textPayload=~"{grep}" OR jsonPayload.message=~"{grep}")')
    if extra:
        parts.append(f"({extra})")
    return " ".join(parts)


def _entry_text(e: dict) -> str:
    if "textPayload" in e:
        return e["textPayload"]
    p = e.get("jsonPayload") or e.get("protoPayload") or {}
    return p.get("message") or json.dumps(p)[:2000]


def read_logs(filt: str, token: str, *, limit: int = 200, project: str = PROJECT) -> list[dict]:
    out, page = [], None
    while len(out) < limit:
        body = {"resourceNames": [f"projects/{project}"], "filter": filt,
                "orderBy": "timestamp desc", "pageSize": min(1000, limit - len(out))}
        if page:
            body["pageToken"] = page
        res = _request("https://logging.googleapis.com/v2/entries:list", token=token, timeout=60,
                       data=json.dumps(body).encode())
        out += res.get("entries", [])
        page = res.get("nextPageToken")
        if not page:
            break
    return out


def gcs_get(uri: str, dest: str, token: str) -> int:
    m = re.match(r"^gs://([^/]+)/(.+)$", uri)
    if not m:
        raise ValueError(f"expected gs://bucket/object, got {uri!r}")
    url = (f"https://storage.googleapis.com/storage/v1/b/{m[1]}/o/"
           f"{urllib.parse.quote(m[2], safe='')}?alt=media")
    data = _request(url, token=token, raw=True, timeout=120)
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def run_get(path: str, token: str, *, project: str = PROJECT, region: str = REGION) -> dict:
    return _request(f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/{path.lstrip('/')}",
                    token=token)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("secret").add_argument("name")
    sub.add_parser("id-token").add_argument("--audience", default=IAP_CLIENT_ID)
    q = sub.add_parser("bq")
    q.add_argument("sql")
    q.add_argument("--max-rows", type=int, default=1000)
    lg = sub.add_parser("logs")
    lg.add_argument("--job")
    lg.add_argument("--service")
    lg.add_argument("--since", default="1d")
    lg.add_argument("--grep", help="RE2 regex on the log message")
    lg.add_argument("--filter", dest="extra", help="extra Cloud Logging filter clause")
    lg.add_argument("--limit", type=int, default=200)
    g = sub.add_parser("gcs-get")
    g.add_argument("uri")
    g.add_argument("dest")
    sub.add_parser("run").add_argument("path", help="Cloud Run v2 path under the region, e.g. jobs/<job>/executions")
    a = ap.parse_args(argv)
    try:
        federated = federated_access_token(cursor_oidc_token())
        if a.cmd == "id-token":
            sys.stdout.write(sa_id_token(federated, a.audience))
            return 0
        token = sa_access_token(federated)
        if a.cmd == "secret":
            sys.stdout.write(access_secret(a.name, token))
        elif a.cmd == "bq":
            for row in bq_query(a.sql, token, max_rows=a.max_rows):
                print(json.dumps(row, default=str))
        elif a.cmd == "logs":
            filt = logs_filter(job=a.job, service=a.service, since=a.since, grep=a.grep, extra=a.extra)
            for e in reversed(read_logs(filt, token, limit=a.limit)):
                exe = (e.get("labels") or {}).get("run.googleapis.com/execution_name", "")
                print(f"{e.get('timestamp', '')} {e.get('severity', '')} {exe} {_entry_text(e)}".rstrip())
        elif a.cmd == "gcs-get":
            print(f"wrote {gcs_get(a.uri, a.dest, token)} bytes to {a.dest}")
        else:
            print(json.dumps(run_get(a.path, token), indent=1))
    except (OSError, RuntimeError, KeyError, ValueError) as e:
        detail = e.read().decode(errors="replace")[:300] if isinstance(e, urllib.error.HTTPError) else e
        print(f"cloud_agent_gcp: {a.cmd} {getattr(a, 'name', '')} unavailable — {detail}".replace("  ", " "), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
