"""Pins the root-conftest guard that keeps tests from mailing the operator.

See `conftest.py` for the incident this protects against. The interesting case is
not "are the variables unset when nobody set them" — it is "does a shell that
exported the production credentials still produce a silent test run". So the main
test runs pytest in a subprocess with credentials deliberately exported.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import conftest

ROOT = Path(__file__).resolve().parent

PROBE = """
import os

def test_the_shell_credentials_did_not_reach_this_test():
    assert os.environ.get("GMAIL_REFRESH_TOKEN") is None
    assert os.environ.get("GMAIL_CLIENT_SECRET") is None
    assert os.environ.get("GARAGE_GMAIL_REFRESH_TOKEN") is None
    assert os.environ.get("GARAGE_NOTIFY_TO") is None
"""


def test_credentials_are_stripped_even_when_the_shell_exports_them():
    # The probe has to live under the repo root, otherwise the root conftest --
    # the thing under test -- would not apply to it.
    probe_dir = ROOT / ".pytest_probe"
    probe_dir.mkdir(exist_ok=True)
    probe = probe_dir / "test_probe.py"
    probe.write_text(PROBE)
    env = dict(os.environ)
    env.update({
        "GMAIL_REFRESH_TOKEN": "pretend-this-is-the-real-one",
        "GMAIL_CLIENT_SECRET": "pretend-this-is-the-real-one",
        "GARAGE_GMAIL_REFRESH_TOKEN": "pretend-this-is-the-real-one",
        "GARAGE_NOTIFY_TO": "someone@example.com",
    })
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(probe)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
        )
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)  # the subprocess leaves __pycache__
    assert done.returncode == 0, done.stdout + done.stderr


def test_every_credential_the_notifiers_read_is_covered():
    """A credential a notifier honours but the guard forgets is the failure mode.

    Discovered from the source rather than listed here, so that renaming a
    credential (as Issue #316 did, GMAIL_* -> GARAGE_GMAIL_*) cannot quietly move
    a send path back outside the guard.
    """
    read_by_notifiers = set()
    for module in ("cloud/pup_watch/notify.py", "cloud/tesla_aladdin_garage/notify.py"):
        text = (ROOT / module).read_text()
        for name in re.findall(r"""environ\.get\(\s*["']([A-Z0-9_]+)["']""", text):
            if "GMAIL" in name or "NOTIFY_TO" in name:
                read_by_notifiers.add(name)
    assert len(read_by_notifiers) >= 6, f"suspiciously few found: {read_by_notifiers}"
    missing = read_by_notifiers - set(conftest.LIVE_CREDENTIAL_ENV)
    assert not missing, f"outbound credentials the root guard does not strip: {missing}"
