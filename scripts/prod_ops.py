#!/usr/bin/env python3
"""Prod operations for agents without GCP identity, approval-gated unless provably safe.

A `/prod-ops …` PR comment runs `.github/workflows/prod-ops.yml`. `tier()` sorts it:
- auto — cannot change data the operator relies on (read-only, additive migrations,
  an ADP schedule dry run after its no-write tests pass on the PR head). Runs at once
  in Environment `prod-auto` (no reviewer; the deployment list there is the audit log).
- gated — everything else. Waits in Environment `prod` until the operator approves
  (GitHub web or mobile); the workflow refuses unless `prod` has a required reviewer.

    /prod-ops whoami                                  # no mutation — proves the approval flow
    /prod-ops console-env set CONSOLE_FOO=1 BAR=x     # operator-console runtime flags
    /prod-ops console-env unset CONSOLE_FOO
    /prod-ops run-job bhaga-daily-refresh [REFRESH_DATE=2026-10-10 …]
    /prod-ops apply-migrations                        # core.datastore.ensure_schema() from the PR head
                                                      # (PROD_OPS_CODE_ROOT); auto when every migration
                                                      # new vs main is additive
    /prod-ops run-job-pr bhaga-daily-refresh [KEY=v …] # PR comments only: build the PR head's image,
                                                      # copy the job as <job>-pr<N> on it, run once, delete the copy.
                                                      # Cloud Agent comments post as cursor[bot]; the guard accepts that login.

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
REGISTRY = f"{REGION}-docker.pkg.dev/{PROJECT}/jarvis-images"
# Jobs whose PR image `docker build .` at the repo root produces.
_PR_IMAGES = {"bhaga-daily-refresh": "bhaga-orchestrator"}
# Set by Cloud Run on the source job; a new job must not inherit them.
_SYSTEM_ANNOTATIONS = {
    "run.googleapis.com/creator", "run.googleapis.com/lastModifier", "run.googleapis.com/operation-id",
    "run.googleapis.com/client-name", "run.googleapis.com/client-version",
}

_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_JOB = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
# Set by the deploy workflow; changing them here would desync prod from git.
_DEPLOY_OWNED = {"BQ_PROJECT", "BQ_DATASET", "PLAID_ENV", "PLAID_WEBHOOK_URL", "CONSOLE_PREVIEW"}
# An unapproved run-job-pr may set only these, and must be an `apply` dry run: any other key
# could steer the job off the dry-run path.
_DRY_RUN_ENV = {"BHAGA_ADP_SCHEDULE_WRITE", "BHAGA_SCHEDULE_DRY_RUN", "BHAGA_SCHEDULE_CHANGES", "BHAGA_STORE"}
# Run on the PR head first: they pin that --dry-run walks the ADP wizards and saves nothing.
_DRY_RUN_TESTS = [sys.executable, "-m", "unittest", "agents.bhaga.scripts.test_adp_schedule_write"]
# Anything that can lose or rewrite rows. Over-matching only costs an approval.
_DESTRUCTIVE_SQL = re.compile(
    r"\b(DROP|DELETE|UPDATE|MERGE|TRUNCATE)\b|OR\s+REPLACE\s+TABLE|EXPIRATION|EXECUTE\s+IMMEDIATE", re.I)


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
    if op == "run-job-pr":
        if not rest or rest[0] not in _PR_IMAGES:
            raise RequestError(f"run-job-pr <job> [KEY=value …]; job is one of {sorted(_PR_IMAGES)}")
        return {"op": op, "job": rest[0], "env": _pairs(rest[1:])}
    raise RequestError(f"unknown operation {op!r}")


def _env_flag(env: dict[str, str]) -> list[str]:
    return ["--update-env-vars=" + ",".join(f"{k}={v}" for k, v in env.items())] if env else []


def pr_job(req: dict, pr: str) -> tuple[str, str]:
    """(copy job name, PR image) for a run-job-pr request."""
    return f"{req['job']}-pr{pr}", f"{REGISTRY}/{_PR_IMAGES[req['job']]}:pr{pr}-verify"


def clone_job_spec(spec: dict, name: str, image: str) -> dict:
    """The exported job spec renamed to `name` and running `image`; settings, SA and secrets unchanged."""
    meta = spec["metadata"]
    meta["name"] = name
    for key in ("uid", "resourceVersion", "generation", "creationTimestamp", "selfLink"):
        meta.pop(key, None)
    for m in (meta, spec["spec"]["template"].get("metadata") or {}):
        notes = m.get("annotations") or {}
        for key in _SYSTEM_ANNOTATIONS & set(notes):
            del notes[key]
    spec.pop("status", None)
    containers = spec["spec"]["template"]["spec"]["template"]["spec"]["containers"]
    containers[0]["image"] = image
    return spec


def clone_job(source: str, name: str, image: str) -> int:
    # Imported here: the workflow's guard job plans with a bare python3 (no requirements installed).
    import yaml  # noqa: PLC0415

    loc = [f"--region={REGION}", f"--project={PROJECT}"]
    out = subprocess.run(["gcloud", "run", "jobs", "describe", source, *loc, "--format=export"],
                         capture_output=True, text=True, check=True).stdout
    path = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / f"{name}.yaml"
    path.write_text(yaml.safe_dump(clone_job_spec(yaml.safe_load(out), name, image)))
    return subprocess.run(["gcloud", "run", "jobs", "replace", str(path), *loc, "--quiet"]).returncode


def commands(req: dict, pr: str | None = None) -> list[list[str]]:
    loc = [f"--region={REGION}", f"--project={PROJECT}"]
    op = req["op"]
    if op == "run-job-pr":
        if not pr:
            raise RequestError("run-job-pr only runs from a PR comment (it builds that PR's head)")
        name, image = pr_job(req, pr)
        return [
            ["gcloud", "auth", "configure-docker", f"{REGION}-docker.pkg.dev", "--quiet"],
            ["docker", "build", "-t", image, "."],
            ["docker", "push", image],
            [sys.executable, str(Path(__file__).resolve()), "clone-job", req["job"], name, image],
            ["gcloud", "run", "jobs", "execute", name, *loc, "--wait", *_env_flag(req["env"])],
        ]
    if op == "whoami":
        return [["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"]]
    if op == "console-env":
        if req["action"] == "set":
            flag = "--update-env-vars=" + ",".join(f"{k}={v}" for k, v in req["env"].items())
        else:
            flag = "--remove-env-vars=" + ",".join(req["keys"])
        return [["gcloud", "run", "services", "update", CONSOLE_SERVICE, *loc, flag, "--quiet"]]
    if op == "run-job":
        return [["gcloud", "run", "jobs", "execute", req["job"], *loc, "--async", *_env_flag(req["env"])]]
    if op == "apply-migrations":
        return [[sys.executable, "-c",
                 "from core.datastore import ensure_schema; print('applied:', ensure_schema() or 'none')"]]
    raise RequestError(f"unknown operation {op!r}")


def cleanup(req: dict, pr: str | None) -> list[list[str]]:
    """Run once the copy step has started, even when a later command failed: the copy never outlives its run."""
    if req["op"] != "run-job-pr" or not pr:
        return []
    name, _ = pr_job(req, pr)
    return [["gcloud", "run", "jobs", "delete", name, f"--region={REGION}", f"--project={PROJECT}", "--quiet"]]


def _changed_vs_main(code_root: Path, pattern: str) -> list[Path]:
    """Files under code_root matching pattern that main (REPO_ROOT) lacks or has with different bytes."""
    out = []
    for p in sorted(code_root.glob(pattern)):
        base = REPO_ROOT / p.relative_to(code_root)
        if not base.is_file() or base.read_bytes() != p.read_bytes():
            out.append(p)
    return out


def new_migrations(code_root: Path) -> list[Path]:
    return _changed_vs_main(code_root, "core/migrations/*.sql")


def _sql_body(path: Path) -> str:
    """The SQL without whole-line `--` comments (headers routinely say "never UPDATE/DELETE")."""
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("--"))


def tier(req: dict, code_root: Path) -> tuple[str, str]:
    """("auto", why) when the op cannot change data the operator relies on, else ("gated", why)."""
    op, env = req["op"], req.get("env", {})
    if op == "whoami":
        return "auto", "read-only"
    if op == "apply-migrations":
        # ensure_schema() is PR-head Python: only main's runner may apply unapproved SQL.
        runner = [str(p.relative_to(code_root)) for p in _changed_vs_main(code_root, "core/*.py")]
        if runner:
            return "gated", f"migration runner differs from main: {', '.join(runner)}"
        risky = [p.name for p in new_migrations(code_root) if _DESTRUCTIVE_SQL.search(_sql_body(p))]
        if risky:
            return "gated", f"migrations that can drop or rewrite rows: {', '.join(risky)}"
        return "auto", "additive migrations only"
    if (op == "run-job-pr" and env.get("BHAGA_ADP_SCHEDULE_WRITE") == "apply"
            and env.get("BHAGA_SCHEDULE_DRY_RUN") == "1" and set(env) <= _DRY_RUN_ENV):
        return "auto", "ADP schedule dry run — saves nothing; its no-write tests run first"
    return "gated", "changes prod"


def _pr_number() -> str | None:
    """The PR this comment is on: IS_PR in the guard, the PR head SHA in the run job."""
    on_pr = os.environ.get("IS_PR") == "true" or bool(os.environ.get("HEAD_SHA"))
    return (os.environ.get("ISSUE") or None) if on_pr else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["plan", "run", "clone-job"])
    ap.add_argument("args", nargs="*", help="clone-job: <source job> <new name> <image>")
    a = ap.parse_args(argv)
    if a.mode == "clone-job":
        return clone_job(*a.args)
    pr = _pr_number()
    cwd = Path(os.environ.get("PROD_OPS_CODE_ROOT") or REPO_ROOT)
    try:
        req = parse_comment(os.environ.get("COMMENT_BODY", ""))
        cmds = commands(req, pr)
    except RequestError as e:
        print(f"prod-ops: rejected — {e}")
        return 2
    level, why = tier(req, cwd)
    if level == "auto" and req["op"] == "run-job-pr":
        cmds = [_DRY_RUN_TESTS, *cmds]
    after = cleanup(req, pr)
    if a.mode == "plan":
        print(json.dumps({"request": req, "tier": level, "why": why,
                          "commands": [shlex.join(c) for c in cmds + after]}))
        return 0
    # The guard picked the environment from its own plan; never run an op it would now gate.
    if os.environ.get("PROD_OPS_TIER") != "gated" and level != "auto":
        print(f"prod-ops: rejected — planned without approval but now needs it ({why})")
        return 2
    if req["op"] == "run-job-pr" and not os.environ.get("PROD_OPS_CODE_ROOT"):
        print("prod-ops: rejected — run-job-pr needs the PR head checkout (PROD_OPS_CODE_ROOT)")
        return 2
    env = {**os.environ, "BHAGA_DATASTORE": "bigquery"}
    rc, copy_started = 0, False
    for c in cmds:
        print(f"prod-ops: $ {shlex.join(c)}  (cwd={cwd})", flush=True)
        copy_started = copy_started or "clone-job" in c
        rc = subprocess.run(c, cwd=cwd, env=env).returncode
        if rc != 0:
            break
    for c in after if copy_started else []:
        print(f"prod-ops: $ {shlex.join(c)}  (cleanup)", flush=True)
        if subprocess.run(c, cwd=cwd, env=env).returncode != 0:
            print(f"BREADCRUMB prod_ops cleanup failed: {shlex.join(c)} — delete it by hand")
    if rc != 0:
        print(f"prod-ops: failed op={req['op']} rc={rc}")
        return rc
    print(f"prod-ops: ok op={req['op']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
