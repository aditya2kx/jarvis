"""Aladdin secret-hash + dry-run open."""

from unittest.mock import patch

from skills.aladdin_connect.client import AladdinConnectClient, secret_hash, door_is_open


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

