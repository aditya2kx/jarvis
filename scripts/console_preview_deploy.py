#!/usr/bin/env python3
"""PR-tagged Operator Console Cloud Run preview (Issue #228 / cloud-verify).

Deploys a revision with ``--no-traffic --tag prN`` so the canonical IAP URL
keeps serving production traffic. Prints the tag host for operator poke /
§4 E0 evidence.

Tag host = separate IAP cookie jar (Issue #208) — sign in again on that host;
do not mix with the canonical ``operator-console-…run.app`` host.

Usage:
    # Build (Cloud Build) + deploy tagged revision for PR 234
    python3 scripts/console_preview_deploy.py --pr 234

    # Deploy an already-pushed image (skip build)
    python3 scripts/console_preview_deploy.py --pr 234 \\
        --image us-central1-docker.pkg.dev/$GCP_PROJECT/jarvis-images/operator-console:abc123

    # Remove the tag after merge (leaves revision; no traffic change)
    python3 scripts/console_preview_deploy.py --pr 234 --remove-tags

Env: ``GCP_PROJECT`` (required unless ``--project``). Needs ``gcloud`` + ADC
or WIF (CI), plus Docker for the default ``--builder docker`` path (same as
``operator-console-deploy.yml``). ``--builder cloudbuild`` needs Cloud Build
bucket IAM. Prefer the GH workflow from a Cloud Agent VM that has no ADC.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE = "operator-console"
REGION = "us-central1"
# Project number for run.app tag URLs (canonical host uses the same number).
PROJECT_NUMBER = "887772634501"
TAG_RE = re.compile(r"^pr\d+$")


def _project(cli: str | None) -> str:
    p = (cli or os.environ.get("GCP_PROJECT") or "").strip()
    if not p:
        raise SystemExit(
            "console_preview_deploy: set --project or GCP_PROJECT "
            "(same project as operator-console Cloud Run)."
        )
    return p


def registry(project: str) -> str:
    return f"{REGION}-docker.pkg.dev/{project}/jarvis-images"


def tag_for_pr(pr: int) -> str:
    tag = f"pr{int(pr)}"
    if not TAG_RE.match(tag):
        raise SystemExit(f"console_preview_deploy: invalid tag {tag!r}")
    return tag


def preview_url(tag: str) -> str:
    """Cloud Run revision-tag URL (IAP cookie jar separate from canonical)."""
    return (
        f"https://{tag}---{SERVICE}-{PROJECT_NUMBER}.{REGION}.run.app"
    )


def _run(cmd: list[str], *, dry_run: bool) -> None:
    print("+", " ".join(cmd))
    if dry_run:
        return
    subprocess.check_call(cmd)


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
    ).strip()


def build_image(*, project: str, image: str, dry_run: bool, builder: str = "docker") -> None:
    """Build+push image. Default ``docker`` matches operator-console-deploy.yml.

    ``cloudbuild`` uses ``gcloud builds submit`` (needs Cloud Build bucket IAM).
    """
    context = REPO_ROOT / "apps" / "operator-console"
    if not (context / "Dockerfile").is_file():
        raise SystemExit(f"console_preview_deploy: missing Dockerfile under {context}")
    if builder == "cloudbuild":
        _run(
            [
                "gcloud", "builds", "submit", str(context),
                f"--project={project}",
                f"--tag={image}",
                "--timeout=1200",
                "--quiet",
            ],
            dry_run=dry_run,
        )
        return
    if builder != "docker":
        raise SystemExit(f"console_preview_deploy: unknown --builder {builder!r}")
    _run(
        ["docker", "build", "-t", image, str(context)],
        dry_run=dry_run,
    )
    _run(["docker", "push", image], dry_run=dry_run)


def deploy_tagged(
    *,
    project: str,
    image: str,
    tag: str,
    dry_run: bool,
) -> str:
    """New revision: no traffic to it; reachable only via tag URL.

    Returns the URL Cloud Run reports (authoritative; may be hash host).
    """
    # Mirror production deploy flags so the revision has IAP + env/secrets.
    # --no-traffic keeps 100% on the current production revision.
    cmd = [
        "gcloud", "run", "deploy", SERVICE,
        f"--image={image}",
        f"--region={REGION}",
        f"--project={project}",
        "--no-allow-unauthenticated",
        "--iap",
        "--memory=512Mi",
        "--min-instances=1",
        "--no-traffic",
        f"--tag={tag}",
        f"--set-env-vars=BQ_PROJECT={project},BQ_DATASET=bhaga,"
        "PLAID_ENV=production,"
        "PLAID_WEBHOOK_URL=https://bhaga-webhook-4yl5izovxq-uc.a.run.app/plaid/webhook",
        "--set-secrets="
        "GEMINI_TOKEN=operator-console-gemini-token:latest,"
        "PLAID_CLIENT_ID=plaid_client_id:latest,"
        "PLAID_SECRET=plaid_secret:latest,"
        "CLICKUP_PAT=jarvis-clickup-palmetto-pat:latest",
    ]
    print("+", " ".join(cmd))
    if dry_run:
        return preview_url(tag)
    completed = subprocess.run(cmd, check=True, text=True, capture_output=True)
    # gcloud prints both stdout and progress on stderr
    combined = (completed.stdout or "") + "\n" + (completed.stderr or "")
    print(combined)
    m = re.search(
        r"https://[a-z0-9-]+---" + re.escape(SERVICE) + r"[a-z0-9.-]*\.a\.run\.app",
        combined,
    )
    if m:
        return m.group(0)
    return preview_url(tag)


def verify_preview_url(url: str, *, dry_run: bool = False) -> None:
    """Assert the tag host is live: HTTP 302 → accounts.google.com (IAP).

    Fails loud on 404/DNS so we never hand the operator a dead link.
    Does not complete OAuth (no browser session here).
    """
    if dry_run:
        print(f"(dry-run) would verify IAP challenge on {url}")
        return
    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, method="HEAD")
        # Don't follow redirects — we want the IAP 302 itself.
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
        # Replace redirect handler with one that doesn't follow
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            opener.open(req, timeout=20)
            raise SystemExit(
                f"console_preview_deploy: verify failed for {url}: "
                f"expected IAP 302, got 2xx/3xx without redirect"
            )
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise SystemExit(
                    f"console_preview_deploy: verify failed for {url}: "
                    f"HTTP {exc.code} (want IAP 302 → accounts.google.com)"
                ) from exc
            loc = exc.headers.get("Location") or ""
            if "accounts.google.com" not in loc:
                raise SystemExit(
                    f"console_preview_deploy: verify failed for {url}: "
                    f"redirect Location not Google IAP ({loc[:120]!r})"
                )
            print(f"Verified IAP challenge: {url} → accounts.google.com (HTTP {exc.code})")
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(
            f"console_preview_deploy: verify failed for {url}: {exc}"
        ) from exc


def remove_tags(*, project: str, tag: str, dry_run: bool) -> None:
    _run(
        [
            "gcloud", "run", "services", "update-traffic", SERVICE,
            f"--region={REGION}",
            f"--project={project}",
            f"--remove-tags={tag}",
        ],
        dry_run=dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pr", type=int, required=True, help="PR number → tag prN")
    ap.add_argument("--project", default=None, help="GCP project (default: GCP_PROJECT)")
    ap.add_argument(
        "--image",
        default=None,
        help="Full image ref; default builds operator-console:prN-<sha>",
    )
    ap.add_argument(
        "--skip-build",
        action="store_true",
        help="Require --image; do not Cloud Build",
    )
    ap.add_argument(
        "--remove-tags",
        action="store_true",
        help="Remove prN tag from the service (post-merge cleanup)",
    )
    ap.add_argument(
        "--builder",
        choices=("docker", "cloudbuild"),
        default="docker",
        help="Image build backend (default: docker, same as operator-console-deploy.yml)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    project = _project(args.project)
    tag = tag_for_pr(args.pr)
    url = preview_url(tag)

    if args.remove_tags:
        remove_tags(project=project, tag=tag, dry_run=args.dry_run)
        print(f"Removed tag {tag}. Canonical traffic unchanged.")
        print(f"(was) {url}")
        return 0

    image = args.image
    if image is None:
        if args.skip_build:
            raise SystemExit("console_preview_deploy: --skip-build requires --image")
        sha = _git_sha()
        image = f"{registry(project)}/{SERVICE}:{tag}-{sha}"
        build_image(
            project=project, image=image, dry_run=args.dry_run, builder=args.builder,
        )
    elif not args.skip_build:
        # Explicit --image: deploy that digest/tag as-is (no Cloud Build).
        pass

    deploy_url = deploy_tagged(
        project=project, image=image, tag=tag, dry_run=args.dry_run,
    )
    verify_preview_url(deploy_url, dry_run=args.dry_run)

    print()
    print("─── CONSOLE PREVIEW ───")
    print(f"Tag:      {tag}")
    print(f"Image:    {image}")
    print(f"URL:      {deploy_url}")
    alt = preview_url(tag)
    if deploy_url.rstrip("/") != alt.rstrip("/"):
        print(f"Alt URL:  {alt}  (project-number form; prefer gcloud URL above)")
    print("Canonical traffic: unchanged (this revision has 0%).")
    print(
        "IAP: sign in again on this host — separate cookie jar from "
        "operator-console-…run.app (Issue #208)."
    )
    print(
        "Mobile/Cursor tip: Cloud Run tag URLs contain '---'; paste the full "
        "URL into the browser address bar — in-app link openers often mangle it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
