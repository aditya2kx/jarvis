#!/usr/bin/env python3
"""Read-only prod access for agents without GCP identity (Cursor Cloud Agents).

The agent posts a PR comment; `.github/workflows/prod-read.yml` runs it with the
CI workload identity and uploads the result **encrypted to a public key the agent
put in the comment**. This repo is public — Actions logs, comments and artifacts
are world-readable — so plaintext prod data never leaves the runner, and the
only cleartext output is a one-line summary.

Agent side:

    python3 scripts/prod_read.py keygen              # once per VM; prints the pubkey
    # PR comment (via the agent's PR-comment tool):
    #   /prod-read status
    #   /prod-read bq            + a ```sql fenced block (SELECT / WITH only)
    #   /prod-read logs --service operator-console --since 2h [--grep text]
    #   /prod-read logs --job bhaga-daily-refresh
    #   /prod-read describe --service operator-console
    #   pubkey: <line printed by keygen>
    gh run download <run-id> -n prod-read-<comment-id> -D /tmp/pr
    python3 scripts/prod_read.py decrypt /tmp/pr/result.enc.json

Runner side: `serve-comment` (parse → run → encrypt) and `self-test`.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = "jarvis-bhaga-prod"
REGION = "us-central1"
MAX_ROWS = 200
MAX_BYTES_BILLED = 5 * 1024**3
MAX_LOG_ENTRIES = 200
KEY_DIR = Path.home() / ".prod_read"
REPO_ROOT = Path(__file__).resolve().parent.parent

_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_SINCE = re.compile(r"^\d{1,3}[mhd]$")
_WRITE_WORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CALL|EXPORT|LOAD|"
    r"DECLARE|SET|BEGIN|COMMIT|ROLLBACK|EXECUTE)\b",
    re.IGNORECASE,
)


class RequestError(ValueError):
    pass


# ── request parsing ──────────────────────────────────────────────────────────

def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"(--|#)[^\n]*", " ", sql)


def check_select_only(sql: str) -> str:
    """Text-level guard; the runner also requires BigQuery's dry-run statement_type == SELECT."""
    body = _strip_sql_comments(sql).strip().rstrip(";").strip()
    if not body:
        raise RequestError("empty SQL")
    if ";" in body:
        raise RequestError("one statement only")
    if not re.match(r"^(SELECT|WITH)\b", body, re.IGNORECASE):
        raise RequestError("SQL must start with SELECT or WITH")
    hit = _WRITE_WORDS.search(re.sub(r"'(?:[^'\\]|\\.)*'|`[^`]*`", "''", body))
    if hit:
        raise RequestError(f"write keyword not allowed: {hit.group(1).upper()}")
    return body


def parse_comment(body: str) -> dict:
    lines = body.strip().splitlines()
    if not lines or not lines[0].startswith("/prod-read"):
        raise RequestError("comment must start with /prod-read")
    argv = shlex.split(lines[0])[1:]
    if not argv:
        raise RequestError("missing operation: status | bq | logs | describe")
    op, rest = argv[0], argv[1:]

    pub = re.search(r"^pubkey:\s*([A-Za-z0-9+/=]+)\s*$", body, re.MULTILINE)
    if not pub:
        raise RequestError("missing 'pubkey: <base64>' line (python3 scripts/prod_read.py keygen)")
    req: dict = {"op": op, "pubkey": pub.group(1)}

    ap = argparse.ArgumentParser(prog=f"/prod-read {op}", add_help=False, exit_on_error=False)
    if op == "status":
        ap.add_argument("--store", default="palmetto")
        ap.add_argument("--date")
    elif op == "bq":
        m = re.search(r"```sql\s*\n(.*?)```", body, re.DOTALL)
        if not m:
            raise RequestError("bq needs a ```sql fenced block")
        req["sql"] = check_select_only(m.group(1))
    elif op in ("logs", "describe"):
        g = ap.add_mutually_exclusive_group(required=True)
        g.add_argument("--service")
        g.add_argument("--job")
        if op == "logs":
            ap.add_argument("--since", default="1h")
            ap.add_argument("--grep")
    else:
        raise RequestError(f"unknown operation {op!r}: status | bq | logs | describe")

    try:
        ns = ap.parse_args(rest)
    except (argparse.ArgumentError, SystemExit) as e:
        raise RequestError(f"bad arguments for {op}: {e}") from None
    for k, v in vars(ns).items():
        if v is not None:
            req[k] = v

    for k in ("service", "job", "store"):
        if k in req and not _NAME.match(req[k]):
            raise RequestError(f"invalid --{k}")
    if "since" in req and not _SINCE.match(req["since"]):
        raise RequestError("--since must look like 30m, 2h or 1d")
    if "date" in req and not re.match(r"^\d{4}-\d{2}-\d{2}$", req["date"]):
        raise RequestError("--date must be YYYY-MM-DD")
    if "grep" in req and ('"' in req["grep"] or "\\" in req["grep"]):
        raise RequestError("--grep may not contain quotes or backslashes")
    return req


# ── execution (runner) ───────────────────────────────────────────────────────

def _run(cmd: list[str]) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    if out.returncode != 0:
        raise RuntimeError(f"{cmd[0]} exited {out.returncode}: {out.stderr.strip()[-500:]}")
    return out.stdout


def run_bq(sql: str) -> tuple[dict, str]:
    from google.cloud import bigquery

    client = bigquery.Client(project=PROJECT)
    dry = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    if dry.statement_type != "SELECT":
        raise RequestError(f"BigQuery classified this as {dry.statement_type}, not SELECT")
    job = client.query(sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_BILLED))
    it = job.result(max_results=MAX_ROWS)
    rows = [{k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v)) for k, v in r.items()}
            for r in it]
    cols = [f.name for f in it.schema or []]
    result = {"columns": cols, "rows": rows, "truncated": (it.total_rows or 0) > len(rows)}
    return result, f"bq: {len(rows)} rows, columns={cols}"


def _log_filter(req: dict) -> str:
    if "service" in req:
        f = f'resource.type="cloud_run_revision" AND resource.labels.service_name="{req["service"]}"'
    else:
        f = f'resource.type="cloud_run_job" AND resource.labels.job_name="{req["job"]}"'
    if req.get("grep"):
        f += f' AND "{req["grep"]}"'
    return f


def execute(req: dict) -> tuple[dict | str, str]:
    op = req["op"]
    if op == "bq":
        return run_bq(req["sql"])
    if op == "status":
        cmd = [sys.executable, "-m", "agents.bhaga.scripts.status", "--store", req.get("store", "palmetto"), "--json"]
        if req.get("date"):
            cmd += ["--date", req["date"]]
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
        return {"exit_code": out.returncode, "stdout": out.stdout, "stderr": out.stderr[-4000:]}, f"status: exit={out.returncode}"
    if op == "logs":
        out = _run([
            "gcloud", "logging", "read", _log_filter(req), f"--project={PROJECT}",
            f"--freshness={req.get('since', '1h')}", f"--limit={MAX_LOG_ENTRIES}", "--format=json",
        ])
        entries = json.loads(out or "[]")
        return {"entries": entries}, f"logs: {len(entries)} entries"
    if op == "describe":
        kind, name = ("services", req["service"]) if "service" in req else ("jobs", req["job"])
        out = _run(["gcloud", "run", kind, "describe", name, f"--region={REGION}", f"--project={PROJECT}", "--format=json"])
        return json.loads(out), f"describe: {kind[:-1]} {name}"
    raise RequestError(f"unknown operation {op!r}")


# ── hybrid encryption (RSA-OAEP wraps a random AES-256 key) ──────────────────

def _pub_pem(pub_b64: str, tmp: Path) -> Path:
    der = tmp / "pub.der"
    der.write_bytes(base64.b64decode(pub_b64))
    pem = tmp / "pub.pem"
    _run(["openssl", "pkey", "-pubin", "-inform", "DER", "-in", str(der), "-out", str(pem)])
    return pem


def encrypt(plaintext: bytes, pub_b64: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        pem = _pub_pem(pub_b64, tmp)
        (tmp / "k").write_text(os.urandom(32).hex())
        (tmp / "m").write_bytes(plaintext)
        _run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", f"file:{tmp / 'k'}",
              "-in", str(tmp / "m"), "-out", str(tmp / "m.enc")])
        _run(["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", str(pem),
              "-pkeyopt", "rsa_padding_mode:oaep", "-in", str(tmp / "k"), "-out", str(tmp / "k.enc")])
        return {
            "v": 1,
            "key": base64.b64encode((tmp / "k.enc").read_bytes()).decode(),
            "data": base64.b64encode((tmp / "m.enc").read_bytes()).decode(),
        }


def decrypt(bundle: dict, key_pem: Path) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "k.enc").write_bytes(base64.b64decode(bundle["key"]))
        (tmp / "m.enc").write_bytes(base64.b64decode(bundle["data"]))
        _run(["openssl", "pkeyutl", "-decrypt", "-inkey", str(key_pem),
              "-pkeyopt", "rsa_padding_mode:oaep", "-in", str(tmp / "k.enc"), "-out", str(tmp / "k")])
        _run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", f"file:{tmp / 'k'}",
              "-in", str(tmp / "m.enc"), "-out", str(tmp / "m")])
        return (tmp / "m").read_bytes()


def keygen(key_dir: Path = KEY_DIR) -> str:
    key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = key_dir / "key.pem"
    if not key.exists():
        _run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(key)])
        key.chmod(0o600)
    der = subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-outform", "DER"],
                         capture_output=True, check=True).stdout
    return base64.b64encode(der).decode()


# ── CLI ──────────────────────────────────────────────────────────────────────

def _serve(req: dict, out: Path) -> str:
    result, summary = execute(req)
    payload = json.dumps({"request": {k: v for k, v in req.items() if k != "pubkey"}, "result": result},
                         default=str, indent=1).encode()
    out.write_text(json.dumps(encrypt(payload, req["pubkey"])))
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="create ~/.prod_read/key.pem (once) and print the pubkey line")
    d = sub.add_parser("decrypt", help="decrypt a result.enc.json with ~/.prod_read/key.pem")
    d.add_argument("bundle")
    s = sub.add_parser("serve-comment", help="runner: parse $COMMENT_BODY, run, write encrypted result")
    s.add_argument("--out", required=True)
    sub.add_parser("self-test", help="runner: canned status + bq run, encrypted round-trip, summary only")
    a = ap.parse_args(argv)

    if a.cmd == "keygen":
        print(f"pubkey: {keygen()}")
        return 0
    if a.cmd == "decrypt":
        sys.stdout.write(decrypt(json.loads(Path(a.bundle).read_text()), KEY_DIR / "key.pem").decode())
        return 0
    if a.cmd == "serve-comment":
        try:
            req = parse_comment(os.environ.get("COMMENT_BODY", ""))
            print(f"prod-read: ok {_serve(req, Path(a.out))}")
            return 0
        except RequestError as e:
            print(f"prod-read: rejected — {e}")
            return 2
        except Exception as e:  # noqa: BLE001 — summary line only; never echo data
            print(f"prod-read: failed — {type(e).__name__}: {str(e)[:300]}")
            return 1
    if a.cmd == "self-test":
        with tempfile.TemporaryDirectory() as td:
            kd = Path(td)
            pub = keygen(kd)
            for body in (
                f"/prod-read status\npubkey: {pub}",
                f"/prod-read bq\n```sql\nSELECT COUNT(*) AS n, MAX(date) AS latest FROM `{PROJECT}.bhaga.model_daily`\n```\npubkey: {pub}",
            ):
                req = parse_comment(body)
                out = kd / "r.json"
                summary = _serve(req, out)
                clear = json.loads(decrypt(json.loads(out.read_text()), kd / "key.pem"))
                assert clear["request"]["op"] == req["op"]
                print(f"self-test {req['op']}: {summary}; encrypted {out.stat().st_size} bytes; round-trip ok")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
