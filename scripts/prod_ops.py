#!/usr/bin/env python3
"""Approval-gated prod mutations for agents without GCP identity.

A `/prod-ops …` PR comment runs `.github/workflows/prod-ops.yml`; the mutating
job sits in the GitHub Environment `prod`, so nothing executes until the operator
approves the deployment (GitHub web or mobile app). The workflow refuses to run
at all unless that environment exists with a required reviewer.

    /prod-ops whoami                                  # no mutation — proves the approval flow
    /prod-ops console-env set CONSOLE_FOO=1 BAR=x     # operator-console runtime flags
    /prod-ops console-env unset CONSOLE_FOO
    /prod-ops run-job bhaga-daily-refresh [REFRESH_DATE=2026-10-10 …]
    /prod-ops apply-migrations                        # core.datastore.ensure_schema() from the PR head
                                                      # (PROD_OPS_CODE_ROOT; migrations are additive)

`plan` prints exactly what will run (shown in the approval request); `run` executes it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

PROJECT = "jarvis-bhaga-prod"
REGION = "us-central1"
CONSOLE_SERVICE = "operator-console"
REPO_ROOT = Path(__file__).resolve().parent.parent

_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_JOB = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
# Set by the deploy workflow; changing them here would desync prod from git.
_DEPLOY_OWNED = {"BQ_PROJECT", "BQ_DATASET", "PLAID_ENV", "PLAID_WEBHOOK_URL", "CONSOLE_PREVIEW"}


class RequestError(ValueError):
    pass


def _pairs(tokens: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in tokens:
        k, sep, v = t.partition("=")
        if not sep or not _KEY.match(k):
            raise RequestError(f"expected KEY=value, got {t!r}")
        if "," in v:
            raise RequestError(f"{k}: commas are not allowed in values")
        out[k] = v
    return out


def parse_comment(body: str) -> dict:
    first = (body or "").strip().splitlines()[0] if (body or "").strip() else ""
    if not first.startswith("/prod-ops"):
        raise RequestError("comment must start with /prod-ops")
    argv = shlex.split(first)[1:]
    if not argv:
        raise RequestError("missing operation: whoami | console-env | run-job | apply-migrations")
    op, rest = argv[0], argv[1:]
    if op in ("whoami", "apply-migrations"):
        if rest:
            raise RequestError(f"{op} takes no arguments")
        return {"op": op}
    if op == "console-env":
        if not rest or rest[0] not in ("set", "unset") or len(rest) < 2:
            raise RequestError("console-env set KEY=value … | console-env unset KEY …")
        if rest[0] == "set":
            env = _pairs(rest[1:])
            keys = list(env)
        else:
            keys = rest[1:]
            env = {}
            for k in keys:
                if not _KEY.match(k):
                    raise RequestError(f"invalid key {k!r}")
        owned = sorted(set(keys) & _DEPLOY_OWNED)
        if owned:
            raise RequestError(f"{owned} are owned by operator-console-deploy.yml — change them in git")
        return {"op": op, "action": rest[0], "env": env, "keys": keys}
    if op == "run-job":
        if not rest or not _JOB.match(rest[0]):
            raise RequestError("run-job <job-name> [KEY=value …]")
        return {"op": op, "job": rest[0], "env": _pairs(rest[1:])}
    raise RequestError(f"unknown operation {op!r}")


def commands(req: dict) -> list[list[str]]:
    loc = [f"--region={REGION}", f"--project={PROJECT}"]
    op = req["op"]
    if op == "whoami":
        return [["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"]]
    if op == "console-env":
        if req["action"] == "set":
            flag = "--update-env-vars=" + ",".join(f"{k}={v}" for k, v in req["env"].items())
        else:
            flag = "--remove-env-vars=" + ",".join(req["keys"])
        return [["gcloud", "run", "services", "update", CONSOLE_SERVICE, *loc, flag, "--quiet"]]
    if op == "run-job":
        cmd = ["gcloud", "run", "jobs", "execute", req["job"], *loc, "--async"]
        if req["env"]:
            cmd.append("--update-env-vars=" + ",".join(f"{k}={v}" for k, v in req["env"].items()))
        return [cmd]
    if op == "apply-migrations":
        return [[sys.executable, "-c",
                 "from core.datastore import ensure_schema; print('applied:', ensure_schema() or 'none')"]]
    raise RequestError(f"unknown operation {op!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["plan", "run"])
    a = ap.parse_args(argv)
    try:
        req = parse_comment(os.environ.get("COMMENT_BODY", ""))
    except RequestError as e:
        print(f"prod-ops: rejected — {e}")
        return 2
    cmds = commands(req)
    if a.mode == "plan":
        print(json.dumps({"request": req, "commands": [shlex.join(c) for c in cmds]}))
        return 0
    env = {**os.environ, "BHAGA_DATASTORE": "bigquery"}
    cwd = os.environ.get("PROD_OPS_CODE_ROOT") or REPO_ROOT
    for c in cmds:
        print(f"prod-ops: $ {shlex.join(c)}  (cwd={cwd})", flush=True)
        rc = subprocess.run(c, cwd=cwd, env=env).returncode
        if rc != 0:
            print(f"prod-ops: failed op={req['op']} rc={rc}")
            return rc
    print(f"prod-ops: ok op={req['op']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
