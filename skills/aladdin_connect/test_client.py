"""Aladdin secret-hash + dry-run open."""

from unittest.mock import patch

from skills.aladdin_connect.client import AladdinConnectClient, secret_hash


def test_secret_hash_stable():
    h = secret_hash("user@example.com")
    assert h == secret_hash("user@example.com")
    assert secret_hash("other") != h


def test_dry_run_open_does_not_http():
    c = AladdinConnectClient("u", "p", dry_run=True)
    with patch.object(c, "_api") as api:
        out = c.open_door("dev1", 1)
    api.assert_not_called()
    assert out["dry_run"] is True
