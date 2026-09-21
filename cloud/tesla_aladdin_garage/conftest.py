"""Garage tests must never reach the live Gmail account (Issue #316).

`GarageWorker` defaults `notify` to the real sender, so any test that exercises
an open path emails the operator when the shell has sourced
`local/tesla-aladdin-garage.env`. Strip the credentials for every test here.
"""

from __future__ import annotations

import pytest

_GMAIL_ENV = (
    "GARAGE_GMAIL_CLIENT_ID",
    "GARAGE_GMAIL_CLIENT_SECRET",
    "GARAGE_GMAIL_REFRESH_TOKEN",
    # pup-watch's names: stripped too, so a shell that sourced its env cannot
    # reach this mailer even if the garage ever grew a fallback.
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "GMAIL_REFRESH_TOKEN",
    "GARAGE_NOTIFY_TO",
    "GARAGE_NOTIFY_FORCE",
    "K_SERVICE",
)


@pytest.fixture(autouse=True)
def _no_live_gmail(monkeypatch):
    for key in _GMAIL_ENV:
        monkeypatch.delenv(key, raising=False)
