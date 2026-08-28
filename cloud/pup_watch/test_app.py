"""HTTP surface: auth is mandatory, start/stop flips the session, /tick delegates."""

import pytest

from cloud.pup_watch import app as app_module
from cloud.pup_watch import persist, worker

TOKEN = "test-token"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PUPWATCH_ADMIN_TOKEN", TOKEN)
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


@pytest.fixture
def store(monkeypatch):
    docs = {"config": {}, "session": {}, "state": {}}
    monkeypatch.setattr(persist, "load_config", lambda: dict(docs["config"]))
    monkeypatch.setattr(persist, "load_session", lambda: dict(docs["session"]))
    monkeypatch.setattr(persist, "load_state", lambda: dict(docs["state"]))
    monkeypatch.setattr(persist, "save_session", lambda s: docs["session"].update(s))
    monkeypatch.setattr(persist, "save_state", lambda s: docs["state"].update(s))
    return docs


def _auth():
    return {app_module.TOKEN_HEADER: TOKEN}


def test_health_needs_no_token(client, store):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["service"] == "pup-watch"
    assert body["cameras"] == ["sm-yard"]
    assert body["monitoring"] is False


def test_health_reports_monitoring_and_recipient_count(client, store, monkeypatch):
    monkeypatch.setenv("PUPWATCH_NOTIFY_TO", "a@example.com,b@example.com")
    store["session"].update({"active": True})
    body = client.get("/health").get_json()
    assert body["monitoring"] is True
    assert body["recipients"] == 2


@pytest.mark.parametrize("method,path", [
    ("post", "/tick"),
    ("get", "/session"),
    ("post", "/session/start"),
    ("post", "/session/stop"),
])
def test_all_control_endpoints_require_a_token(client, store, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_wrong_token_is_rejected(client, store):
    resp = client.post("/tick", headers={app_module.TOKEN_HEADER: "nope"})
    assert resp.status_code == 401


def test_unset_admin_token_refuses_rather_than_failing_open(client, store, monkeypatch):
    """An open /session/start would let anyone burn the free tier and email us."""
    monkeypatch.delenv("PUPWATCH_ADMIN_TOKEN", raising=False)
    assert client.post("/session/start", headers=_auth()).status_code == 401


def test_start_then_status_then_stop(client, store, monkeypatch):
    monkeypatch.setattr(worker, "tick", lambda **k: {"polled": False, "reason": "stub"})

    started = client.post("/session/start", headers=_auth(), json={"hours": 3}).get_json()
    assert started["ok"] is True
    assert started["hours"] == 3
    assert store["session"]["active"] is True

    status = client.get("/session", headers=_auth()).get_json()
    assert status["active"] is True

    stopped = client.post("/session/stop", headers=_auth()).get_json()
    assert stopped["active"] is False
    assert store["session"]["active"] is False


def test_start_clamps_hours_to_the_session_ceiling(client, store):
    body = client.post("/session/start", headers=_auth(), json={"hours": 999}).get_json()
    assert body["hours"] == 12.0


def test_start_clamps_absurdly_small_hours(client, store):
    body = client.post("/session/start", headers=_auth(), json={"hours": 0.01}).get_json()
    assert body["hours"] == 0.25


def test_start_tolerates_a_junk_hours_value(client, store):
    body = client.post("/session/start", headers=_auth(), json={"hours": "soon"}).get_json()
    assert body["hours"] == 12.0


def test_start_accepts_no_body(client, store):
    assert client.post("/session/start", headers=_auth()).status_code == 200


def test_start_resets_episode_state_so_a_new_session_can_notify(client, store):
    store["state"].update({"episode_active": True, "last_notified_ts": 123.0})
    client.post("/session/start", headers=_auth())
    assert store["state"]["episode_active"] is False
    assert store["state"]["last_notified_ts"] is None


def test_start_records_selected_cameras(client, store):
    client.post("/session/start", headers=_auth(), json={"cameras": ["sm-yard"]})
    assert store["session"]["cameras"] == ["sm-yard"]


def test_start_without_cameras_means_all_of_them(client, store):
    client.post("/session/start", headers=_auth(), json={})
    assert store["session"]["cameras"] is None


def test_tick_delegates_to_the_worker(client, store, monkeypatch):
    calls = {"n": 0}

    def fake_tick(**kwargs):
        calls["n"] += 1
        return {"polled": True, "notified": False, "cameras": []}

    monkeypatch.setattr(worker, "tick", fake_tick)
    body = client.post("/tick", headers=_auth()).get_json()
    assert calls["n"] == 1
    assert body["ok"] is True
    assert body["polled"] is True
