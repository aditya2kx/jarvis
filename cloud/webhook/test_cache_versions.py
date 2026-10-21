"""Console query-cache version bumps from BigQuery audit-log pushes."""

from __future__ import annotations

import base64
import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SLACK_SIGNING_SECRET", "test_signing_secret_1234")

import cache_versions  # noqa: E402

with patch("google.cloud.firestore.Client"):
    import handler  # noqa: E402

P, D = "jarvis-bhaga-prod", "bhaga"


def _entry(resource: str, *events: str) -> dict:
    return {"protoPayload": {"resourceName": resource, "metadata": {e: {} for e in events}}}


def _push(entry: dict) -> dict:
    return {"message": {"data": base64.b64encode(json.dumps(entry).encode()).decode()}}


@pytest.mark.parametrize(
    "resource,events,expected",
    [
        (f"projects/{P}/datasets/{D}/tables/model_daily", ["tableDataChange"], ["model_daily"]),
        (f"projects/{P}/datasets/{D}/tables/orders$20261021", ["tableDataChange"], ["orders"]),
        (f"projects/{P}/datasets/{D}/tables/v_payroll", ["tableChange"], ["v_payroll", "__schema__"]),
        (f"projects/{P}/datasets/{D}/tables/new_t", ["tableCreation"], ["new_t", "__schema__"]),
        (f"projects/{P}/datasets/{D}/routines/fn_x", ["routineChange"], ["__schema__"]),
        (f"projects/{P}/datasets/bhaga_sandbox/tables/model_daily", ["tableDataChange"], []),
        (f"projects/other/datasets/{D}/tables/model_daily", ["tableDataChange"], []),
        (f"projects/{P}/datasets/{D}/tables/model_daily", ["tableDataRead"], []),
        ("projects/x/jobs/abc", ["jobChange"], []),
    ],
)
def test_docs_to_bump(resource, events, expected):
    assert cache_versions.docs_to_bump(_entry(resource, *events), P, D) == expected


def test_decode_push_round_trip_and_malformed():
    e = _entry(f"projects/{P}/datasets/{D}/tables/t", "tableDataChange")
    assert cache_versions.decode_push(_push(e)) == e
    assert cache_versions.decode_push({}) is None
    assert cache_versions.decode_push({"message": {"data": "!!not-base64-json"}}) is None


def test_bump_uses_atomic_increments_in_one_batch():
    db = MagicMock()
    fs = MagicMock()
    cache_versions.bump(db, ["a", "__schema__", "a"], source="audit-log", firestore_module=fs)
    batch = db.batch.return_value
    # two distinct version docs + the meta doc, one commit
    assert batch.set.call_count == 3
    batch.commit.assert_called_once()
    payload = batch.set.call_args_list[0].args[1]
    assert payload["v"] is fs.Increment.return_value
    assert payload["source"] == "audit-log"
    fs.Increment.assert_called_with(1)


def test_bump_nothing_is_a_no_op():
    db = MagicMock()
    cache_versions.bump(db, [], source="audit-log", firestore_module=MagicMock())
    db.batch.return_value.commit.assert_not_called()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(handler, "_CACHE_PUSH_AUDIENCE", "https://hook.example/cache/bq-write")
    monkeypatch.setattr(handler, "_CACHE_PUSH_SA", "console-cache-push@p.iam.gserviceaccount.com")
    monkeypatch.setattr(handler, "db", MagicMock())
    handler.app.testing = True
    return handler.app.test_client()


def _claims(email="console-cache-push@p.iam.gserviceaccount.com", verified=True):
    return {"email": email, "email_verified": verified}


def test_route_bumps_on_valid_push(client):
    e = _entry(f"projects/{P}/datasets/{D}/tables/store_config", "tableDataChange")
    with patch.object(handler.id_token, "verify_oauth2_token", return_value=_claims()) as v, \
         patch.object(handler.cache_versions, "bump") as bump:
        r = client.post("/cache/bq-write", json=_push(e), headers={"Authorization": "Bearer t"})
    assert r.status_code == 204
    assert v.call_args.kwargs["audience"] == "https://hook.example/cache/bq-write"
    bump.assert_called_once()
    assert bump.call_args.args[1] == ["store_config"]
    assert bump.call_args.kwargs["source"] == "audit-log"


@pytest.mark.parametrize(
    "headers,claims,side_effect",
    [
        ({}, None, None),
        ({"Authorization": "Bearer t"}, None, ValueError("bad token")),
        ({"Authorization": "Bearer t"}, _claims(email="someone@else.com"), None),
        ({"Authorization": "Bearer t"}, _claims(verified=False), None),
    ],
)
def test_route_rejects_bad_tokens(client, headers, claims, side_effect):
    e = _entry(f"projects/{P}/datasets/{D}/tables/t", "tableDataChange")
    with patch.object(handler.id_token, "verify_oauth2_token", return_value=claims, side_effect=side_effect), \
         patch.object(handler.cache_versions, "bump") as bump:
        r = client.post("/cache/bq-write", json=_push(e), headers=headers)
    assert r.status_code == 403
    bump.assert_not_called()


def test_route_fails_closed_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(handler, "_CACHE_PUSH_SA", "")
    r = client.post("/cache/bq-write", json={}, headers={"Authorization": "Bearer t"})
    assert r.status_code == 503


def test_route_acks_malformed_and_foreign_entries(client):
    foreign = _entry(f"projects/{P}/datasets/bhaga_sandbox/tables/t", "tableDataChange")
    with patch.object(handler.id_token, "verify_oauth2_token", return_value=_claims()), \
         patch.object(handler.cache_versions, "bump") as bump:
        assert client.post("/cache/bq-write", json={"message": {}},
                           headers={"Authorization": "Bearer t"}).status_code == 204
        assert client.post("/cache/bq-write", json=_push(foreign),
                           headers={"Authorization": "Bearer t"}).status_code == 204
    bump.assert_not_called()
