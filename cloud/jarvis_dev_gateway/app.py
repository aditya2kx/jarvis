"""jarvis-dev gateway: start and check Jarvis dev tasks from Siri, Grok or any HTTP client.

A task is a GitHub tracking issue plus an intake comment posted as the bot, served
by `jarvis-dev-signals.yml` exactly like a typed one: `/jarvis-new-task` (default)
starts a Cursor Cloud Agent, `/jarvis-new-task mac` (where="mac") queues it for the Mac
listener. This service holds no Cursor key and never touches prod.

    POST /task   {"text": "...", "where": "cloud"|"mac"} → opens the issue, returns a speakable reply
    GET  /status                          → the newest open jarvis-work issues + agent links
    POST /mcp    JSON-RPC (MCP Streamable HTTP, stateless) → tools start_task / task_status
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

from flask import Flask, Response, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jarvis_dev_gateway")

app = Flask(__name__)

REPO = os.environ.get("GATEWAY_REPO", "aditya2kx/jarvis")
LABEL = "jarvis-work"
MAX_TEXT = 4000
MCP_PROTOCOL = "2025-06-18"
INTAKE = {"cloud": "/jarvis-new-task", "mac": "/jarvis-new-task mac"}
_AGENT_LINK = re.compile(r"Cursor Cloud Agent\*\* started: (\S+)")


class GitHubError(RuntimeError):
    pass


def _gh(method: str, path: str, body: dict | None = None) -> Any:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise GitHubError("GITHUB_TOKEN unset")
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "jarvis-dev-gateway",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raise GitHubError(f"{method} {path} → HTTP {e.code}") from None


def start_task(text: str, where: str = "cloud") -> dict:
    text = (text or "").strip()
    where = (where or "cloud").strip().lower()
    if not text:
        raise ValueError("say what to build")
    if len(text) > MAX_TEXT:
        raise ValueError(f"request is longer than {MAX_TEXT} characters")
    if where not in INTAKE:
        raise ValueError("where must be cloud or mac")
    title = text.splitlines()[0][:100]
    issue = _gh("POST", f"/repos/{REPO}/issues", {
        "title": f"[work] {title}",
        "body": f"{text}\n\n_Opened by the jarvis-dev gateway (voice / Grok / API)._",
        "labels": [LABEL],
    })
    num = issue["number"]
    _gh("POST", f"/repos/{REPO}/issues/{num}/comments", {"body": f"{INTAKE[where]} {text}"})
    log.info("gateway task issue=%s where=%s chars=%d", num, where, len(text))
    follow = ("The agent link will be on the issue in about a minute." if where == "cloud"
              else "It opens on your Mac when the listener is running.")
    return {
        "issue": num,
        "url": issue["html_url"],
        "where": where,
        "speech": f"Started Jarvis task {num}: {title}. {follow}",
    }


def task_status(limit: int = 5) -> dict:
    issues = _gh("GET", f"/repos/{REPO}/issues?labels={LABEL}&state=open&sort=created&direction=desc&per_page={limit}")
    tasks = []
    for it in issues or []:
        if "pull_request" in it:
            continue
        comments = _gh("GET", f"/repos/{REPO}/issues/{it['number']}/comments?per_page=100") or []
        agent = next((m.group(1) for c in reversed(comments) if (m := _AGENT_LINK.search(c.get("body") or ""))), None)
        stage = next((lb["name"][6:] for lb in it.get("labels", []) if lb["name"].startswith("stage:")), "intake")
        tasks.append({"issue": it["number"], "title": it["title"], "url": it["html_url"], "stage": stage, "agent_url": agent})
    if tasks:
        speech = f"{len(tasks)} open tasks. " + " ".join(
            f"Task {t['issue']}, {t['title'].removeprefix('[work] ')}, is at {t['stage']}." for t in tasks[:3]
        )
    else:
        speech = "No open Jarvis tasks."
    return {"tasks": tasks, "speech": speech}


def _authorised(path_token: str | None = None) -> bool:
    expected = os.environ.get("JARVIS_DEV_GATEWAY_TOKEN", "").strip()
    if not expected:
        log.error("gateway fail reason=gateway_token_unset")
        return False
    supplied = path_token or (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def _deny():
    return jsonify({"ok": False, "error": "unauthorized"}), 401


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "jarvis-dev-gateway",
        "repo": REPO,
        "github_token": bool(os.environ.get("GITHUB_TOKEN", "").strip()),
        "gateway_token": bool(os.environ.get("JARVIS_DEV_GATEWAY_TOKEN", "").strip()),
    })


@app.post("/task")
def task():
    if not _authorised():
        return _deny()
    payload = request.get_json(silent=True) or {}
    try:
        text = payload.get("text") or request.form.get("text", "")
        where = payload.get("where") or request.form.get("where", "cloud")
        return jsonify({"ok": True, **start_task(text, where)})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e), "speech": str(e)}), 400
    except GitHubError as e:
        log.error("gateway fail op=task reason=%s", e)
        return jsonify({"ok": False, "error": str(e), "speech": "GitHub refused the task."}), 502


@app.get("/status")
def status():
    if not _authorised():
        return _deny()
    try:
        return jsonify({"ok": True, **task_status()})
    except GitHubError as e:
        log.error("gateway fail op=status reason=%s", e)
        return jsonify({"ok": False, "error": str(e), "speech": "Could not read tasks from GitHub."}), 502


TOOLS = [
    {
        "name": "start_task",
        "description": "Start a Jarvis development task (opens a tracking issue; a Cursor Cloud Agent picks it up, "
                       "or the operator's Mac with where=mac).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "What to build or fix, in plain words."},
                "where": {"type": "string", "enum": ["cloud", "mac"], "default": "cloud"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "task_status",
        "description": "List the newest open Jarvis development tasks with their stage and Cursor agent link.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _rpc_result(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _rpc_error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _handle_rpc(msg: dict) -> dict | None:
    rid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}
    if rid is None:
        return None
    if method == "initialize":
        return _rpc_result(rid, {
            "protocolVersion": params.get("protocolVersion") or MCP_PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "jarvis-dev-gateway", "version": "1"},
        })
    if method == "ping":
        return _rpc_result(rid, {})
    if method == "tools/list":
        return _rpc_result(rid, {"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        try:
            if name == "start_task":
                out = start_task(args.get("text", ""), args.get("where", "cloud"))
            elif name == "task_status":
                out = task_status()
            else:
                return _rpc_error(rid, -32602, f"unknown tool {name}")
        except (ValueError, GitHubError) as e:
            return _rpc_result(rid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
        return _rpc_result(rid, {
            "content": [{"type": "text", "text": out["speech"]}, {"type": "text", "text": json.dumps(out)}],
            "structuredContent": out,
        })
    return _rpc_error(rid, -32601, f"method not found: {method}")


# The grok.com custom-connector dialog takes a URL and OAuth but no header, so the
# token may ride in the path there. It then sits in Cloud Run request logs
# (project-private) — rotate the secret if that URL is ever shared.
@app.post("/mcp")
@app.post("/mcp/<path_token>")
def mcp(path_token: str | None = None):
    if not _authorised(path_token):
        return _deny()
    body = request.get_json(silent=True)
    if body is None:
        return jsonify(_rpc_error(None, -32700, "parse error")), 400
    replies = [r for r in (_handle_rpc(m) for m in (body if isinstance(body, list) else [body])) if r]
    if not replies:
        return Response(status=202)
    return jsonify(replies if isinstance(body, list) else replies[0])
