"""Idempotent log metric + alert if tesla-aladdin-garage poll lines go silent.

Cloud Run CPU-always-on should log `tesla-aladdin-garage poll` about every POLL_INTERVAL_S.
If that line is absent for 3 minutes, something died.

    python3 scripts/ensure_garage_stale_poll_alert.py --project jarvis-bhaga-prod
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

METRIC = "tesla_aladdin_garage_poll"
POLICY_DISPLAY = "tesla-aladdin-garage stale poll"
FILTER = (
    'resource.type="cloud_run_revision" '
    'resource.labels.service_name="tesla-aladdin-garage" '
    'textPayload:"tesla-aladdin-garage poll"'
)


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True)


def ensure_metric(project: str) -> None:
    listed = _run(
        [
            "gcloud",
            "logging",
            "metrics",
            "describe",
            METRIC,
            f"--project={project}",
            "--format=value(name)",
        ]
    )
    if listed.returncode == 0 and listed.stdout.strip():
        print(f"log metric exists: {METRIC}")
        return
    created = _run(
        [
            "gcloud",
            "logging",
            "metrics",
            "create",
            METRIC,
            f"--project={project}",
            f"--description=tesla-aladdin-garage poll breadcrumb",
            f"--log-filter={FILTER}",
        ]
    )
    if created.returncode != 0:
        print(created.stderr, file=sys.stderr)
        raise SystemExit(created.returncode)
    print(f"created log metric: {METRIC}")


def ensure_policy(project: str) -> None:
    raw = _run(
        [
            "gcloud",
            "alpha",
            "monitoring",
            "policies",
            "list",
            f"--project={project}",
            f"--filter=displayName='{POLICY_DISPLAY}'",
            "--format=json",
        ]
    )
    if raw.returncode == 0:
        try:
            policies = json.loads(raw.stdout or "[]")
        except json.JSONDecodeError:
            policies = []
        if policies:
            print(f"alert policy exists: {POLICY_DISPLAY}")
            return
    print(
        "create a Cloud Monitoring alert on metric logging.googleapis.com/user/"
        f"{METRIC} absent 180s (displayName={POLICY_DISPLAY!r}). "
        "Attach a notification channel in the console if none is wired in CI."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="jarvis-bhaga-prod")
    args = parser.parse_args(argv)
    ensure_metric(args.project)
    ensure_policy(args.project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
