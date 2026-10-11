"""Gateway: bearer auth is mandatory, a task is issue + intake comment, MCP speaks JSON-RPC."""

import pytest

pytest.importorskip("flask")

from cloud.jarvis_dev_gateway import app as gw  # noqa: E402

TOKEN = "test-token"


@pytest.fixture
def calls(monkeypatch):
    log = []

    def fake_gh(method, path, body=None):
        log.append((method, path, body))
        if method == "POST" and path.endswith("/issues"):
            return {"number": 401, "html_url": "https://github.com/aditya2kx/jarvis/issues/401"}
        if path.startswith("/repos/aditya2kx/jarvis/issues?"):
            return [
                {"number": 401, "title": "[work] add a chip", "html_url": "u401",
                 "labels": [{"name": "jarvis-work"}, {"name": "stage:build"}]},
                {"number": 400, "title": "a PR", "html_url": "u400", "labels": [], "pull_request": {}},
            ]
        if path.endswith("/comments?per_page=100"):
            return [{"body": "**Cursor Cloud Agent** started: https://cursor.com/agents/bc-1\n\nOpen it"}]
        return {}

    monkeypatch.setattr(gw, "_gh", fake_gh)
    return log


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("JARVIS_DEV_GATEWAY_TOKEN", TOKEN)
    gw.app.config.update(TESTING=True)
    return gw.app.test_client()


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_health_is_open_and_reports_wiring(client, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    body = client.get("/health").get_json()
    assert body["service"] == "jarvis-dev-gateway"
    assert body["gateway_token"] is True and body["github_token"] is False


@pytest.mark.parametrize("path,method", [("/task", "post"), ("/status", "get"), ("/mcp", "post")])
def test_every_action_needs_the_bearer(client, calls, path, method):
    assert getattr(client, method)(path, json={}).status_code == 401
    assert getattr(client, method)(path, json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert calls == []


def test_unset_token_fails_closed(client, calls, monkeypatch):
    monkeypatch.setenv("JARVIS_DEV_GATEWAY_TOKEN", "")
    assert client.post("/task", json={"text": "x"}, headers={"Authorization": "Bearer "}).status_code == 401
    assert calls == []


def test_task_opens_issue_then_posts_intake_comment(client, calls):
    resp = client.post("/task", json={"text": "add a chip to the home page\nmore detail"}, headers=AUTH)
    body = resp.get_json()
    assert resp.status_code == 200 and body["issue"] == 401
    assert "401" in body["speech"]
    (m1, p1, b1), (m2, p2, b2) = calls
    assert (m1, p1) == ("POST", "/repos/aditya2kx/jarvis/issues")
    assert b1["title"] == "[work] add a chip to the home page" and b1["labels"] == ["jarvis-work"]
    assert (m2, p2) == ("POST", "/repos/aditya2kx/jarvis/issues/401/comments")
    assert b2["body"].startswith("/jarvis-cloud-task add a chip")
    assert body["where"] == "cloud"


def test_task_for_the_mac_uses_the_unchanged_mac_intake(client, calls):
    body = client.post("/task", json={"text": "fix it", "where": "mac"}, headers=AUTH).get_json()
    assert calls[1][2]["body"] == "/jarvis-new-task fix it"
    assert "Mac" in body["speech"]


def test_unknown_target_is_refused_without_side_effects(client, calls):
    assert client.post("/task", json={"text": "x", "where": "laptop"}, headers=AUTH).status_code == 400
    assert calls == []


def test_task_accepts_form_text(client, calls):
    assert client.post("/task", data={"text": "fix it"}, headers=AUTH).status_code == 200


def test_empty_or_huge_task_is_refused_without_side_effects(client, calls):
    assert client.post("/task", json={"text": "  "}, headers=AUTH).status_code == 400
    assert client.post("/task", json={"text": "x" * (gw.MAX_TEXT + 1)}, headers=AUTH).status_code == 400
    assert calls == []


def test_github_failure_is_502_with_speech(client, monkeypatch):
    def boom(*a, **k):
        raise gw.GitHubError("POST /issues → HTTP 403")

    monkeypatch.setattr(gw, "_gh", boom)
    resp = client.post("/task", json={"text": "x"}, headers=AUTH)
    assert resp.status_code == 502 and resp.get_json()["speech"]


def test_status_skips_prs_and_finds_agent_link(client, calls):
    body = client.get("/status", headers=AUTH).get_json()
    assert body["tasks"] == [{
        "issue": 401, "title": "[work] add a chip", "url": "u401",
        "stage": "build", "agent_url": "https://cursor.com/agents/bc-1",
    }]
    assert body["speech"].startswith("1 open tasks. Task 401, add a chip, is at build.")


def _rpc(client, method, params=None, rid=1):
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, headers=AUTH)


def test_mcp_initialize_and_list(client, calls):
    init = _rpc(client, "initialize", {"protocolVersion": "2025-03-26"}).get_json()["result"]
    assert init["protocolVersion"] == "2025-03-26" and "tools" in init["capabilities"]
    tools = _rpc(client, "tools/list").get_json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["start_task", "task_status"]


def test_mcp_path_token_for_header_less_connectors(client, calls):
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post(f"/mcp/{TOKEN}", json=rpc).status_code == 200
    assert client.post("/mcp/wrong", json=rpc).status_code == 401


def test_mcp_notification_is_202(client):
    resp = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=AUTH)
    assert resp.status_code == 202


def test_mcp_tool_calls(client, calls):
    out = _rpc(client, "tools/call", {"name": "start_task", "arguments": {"text": "add a chip"}}).get_json()["result"]
    assert out["structuredContent"]["issue"] == 401 and "isError" not in out
    bad = _rpc(client, "tools/call", {"name": "start_task", "arguments": {"text": ""}}).get_json()["result"]
    assert bad["isError"] is True
    assert _rpc(client, "tools/call", {"name": "nope"}).get_json()["error"]["code"] == -32602
    assert _rpc(client, "resources/list").get_json()["error"]["code"] == -32601
