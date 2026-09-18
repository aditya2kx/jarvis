"""Repo-wide test safety: no test may send a real notification.

The garage already has two defences from Issue #316 — its own conftest, and
`notify_runtime_allowed()`, which keeps the sender silent unless `K_SERVICE` says
we are the deployed service. This file is the repo-wide floor underneath them,
added after the same bug bit a *second* service (2026-09-18).

That recurrence is the argument for putting it at the root. While gathering live
pup-watch evidence, a shell had the production `GMAIL_*` credentials exported;
every `pytest cloud/` run in it then mailed the operator six real garage alerts,
because `cloud/tesla_aladdin_garage/test_app.py` posts to `/simulate/enter`, which
runs the real worker and the real notifier. He reported it as a Tesla regression —
nothing about Tesla had changed. Per-module discipline is exactly what failed:
`test_notify.py` did clear the credentials, but only for its own tests, and the
module that actually sent mail never touched them. pup-watch has the same shape
and no runtime gate yet (Issue #320).

Credentials are removed from the environment rather than send functions being
patched, because that is the property the notifiers themselves check: with no
refresh token they log "unconfigured" and skip, which is the same path CI takes.
Tests that need a configured notifier set their own fake values with
`monkeypatch.setenv` — this fixture runs first, so that still works.
"""

from __future__ import annotations

import pytest

# Anything that could reach a human. Add to this list when a new outbound channel
# appears; a missing entry is how the 2026-09-18 recurrence happened.
LIVE_CREDENTIAL_ENV = (
    # pup-watch: sightings, control acks, automatic-stop announcements
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "GMAIL_REFRESH_TOKEN",
    "PUPWATCH_NOTIFY_TO",
    # tesla-aladdin-garage: scoped names from Issue #316, no fallback to the above
    "GARAGE_GMAIL_CLIENT_ID",
    "GARAGE_GMAIL_CLIENT_SECRET",
    "GARAGE_GMAIL_REFRESH_TOKEN",
    "GARAGE_NOTIFY_TO",
    # Overrides that would defeat the runtime gate if a shell happened to set them
    "GARAGE_NOTIFY_FORCE",
    "K_SERVICE",
    # Slack (agent DMs)
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_USER_TOKEN",
)


@pytest.fixture(autouse=True)
def block_live_notifications(monkeypatch):
    """Strip outbound credentials from every test's environment."""
    for name in LIVE_CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)
