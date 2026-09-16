"""Aladdin secret-hash + dry-run open + token expiry / 401 recovery."""

import time
from unittest.mock import patch

import pytest

from skills.aladdin_connect.client import (
    AladdinConnectClient,
    AladdinError,
    secret_hash,
    door_is_open,
)


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


def test_api_uses_access_token_not_id_token():
    c = AladdinConnectClient("u", "p", dry_run=True)
    c._id_token = "id-token"
    c._access_token = "access-token"
    c._access_exp = time.time() + 3600
    with patch("skills.aladdin_connect.client._http", return_value='{"devices":[]}') as http:
        c.list_devices()
    headers = http.call_args.kwargs.get("headers") or http.call_args[1].get("headers")
    assert headers["Authorization"] == "Bearer access-token"


def test_resolve_door_matches_device_id_prefix():
    c = AladdinConnectClient("u", "p", dry_run=True)
    with patch.object(
        c,
        "list_doors",
        return_value=[
            {
                "device_id": "F0AD4E3E7403",
                "serial": "F0AD4E3E7403022",
                "name": "Big Peach",
                "door_index": 1,
            }
        ],
    ):
        door = c.resolve_door(serial="F0AD4E3E7403", door_index=1)
    assert door["device_id"] == "F0AD4E3E7403"


def test_door_is_open_int_and_closed():
    assert door_is_open({"status": 1}) is True
    assert door_is_open({"status": 2}) is True
    assert door_is_open({"status": 3}) is False
    assert door_is_open({"status": 4}) is False
    assert door_is_open({"status": "open"}) is True


def test_token_relogins_when_expired():
    c = AladdinConnectClient("u", "p", dry_run=True)
    c._access_token = "stale"
    c._access_exp = time.time() - 1
    with patch.object(c, "login") as login:
        c._token()
    login.assert_called_once()


def test_token_reused_while_valid():
    c = AladdinConnectClient("u", "p", dry_run=True)
    c._access_token = "fresh"
    c._access_exp = time.time() + 3600
    with patch.object(c, "login") as login:
        assert c._token() == "fresh"
    login.assert_not_called()


def test_login_records_expiry_from_cognito():
    c = AladdinConnectClient("u", "p", dry_run=True)
    fake = {
        "AuthenticationResult": {
            "AccessToken": "access",
            "IdToken": "id",
            "ExpiresIn": 86400,
        }
    }
    before = time.time()
    with patch("skills.aladdin_connect.client._cognito", return_value=fake):
        c.login()
    assert c._access_token == "access"
    assert c._access_exp >= before + 86400 - 5
    assert c._access_exp <= time.time() + 86400 + 5


def test_stale_24h_token_recovers():
    """Regression pin for #310: a 25-hour-old token re-logins and returns devices."""
    c = AladdinConnectClient("u", "p", dry_run=True)
    c._access_token = "expired-25h"
    c._access_exp = time.time() - 25 * 3600

    def fake_login():
        c._access_token = "fresh"
        c._access_exp = time.time() + 86400

    with patch.object(c, "login", side_effect=fake_login) as login:
        with patch(
            "skills.aladdin_connect.client._http",
            return_value='{"devices":[{"id":"1"},{"id":"2"}]}',
        ):
            devices = c.list_devices()
    login.assert_called_once()
    assert len(devices) == 2


def test_api_relogins_once_on_401():
    c = AladdinConnectClient("u", "p", dry_run=True)
    c._access_token = "dead"
    c._access_exp = time.time() + 3600

    def fake_login():
        c._access_token = "fresh"
        c._access_exp = time.time() + 86400

    responses = [
        AladdinError("HTTP 401", status=401, body='{"message":"Unauthorized"}'),
        '{"devices":[{"id":"1"}]}',
    ]

    def fake_http(*_a, **_k):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with patch.object(c, "login", side_effect=fake_login) as login:
        with patch("skills.aladdin_connect.client._http", side_effect=fake_http):
            devices = c.list_devices()
    login.assert_called_once()
    assert len(devices) == 1


def test_api_second_401_propagates():
    c = AladdinConnectClient("u", "p", dry_run=True)
    c._access_token = "dead"
    c._access_exp = time.time() + 3600

    def fake_login():
        c._access_token = "still-dead"
        c._access_exp = time.time() + 86400

    err = AladdinError("HTTP 401", status=401, body='{"message":"Unauthorized"}')
    with patch.object(c, "login", side_effect=fake_login):
        with patch("skills.aladdin_connect.client._http", side_effect=err) as http:
            with pytest.raises(AladdinError) as ei:
                c.list_devices()
    assert ei.value.status == 401
    assert http.call_count == 2


def test_api_does_not_retry_non_401():
    c = AladdinConnectClient("u", "p", dry_run=True)
    c._access_token = "ok"
    c._access_exp = time.time() + 3600
    err = AladdinError("HTTP 500", status=500, body="boom")
    with patch.object(c, "login") as login:
        with patch("skills.aladdin_connect.client._http", side_effect=err) as http:
            with pytest.raises(AladdinError) as ei:
                c.list_devices()
    assert ei.value.status == 500
    assert http.call_count == 1
    login.assert_not_called()
