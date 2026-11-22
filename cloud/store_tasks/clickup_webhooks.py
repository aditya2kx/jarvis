"""ClickUp → Store Tasks in seconds: ClickUp calls a webhook for every change on a store's lists.

The API owns its webhooks: ``ensure`` (scheduled) registers one per configured list and
replaces any ClickUp has suspended or that points elsewhere. Each webhook's signing secret is
kept in ``webhooks`` (server-only). A call updates the task's fields and status, or the
closing count, at once; its comments and files are copied by the next tick (``drain``).
The two-hourly mirror stays as the net for anything a webhook missed.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import time
from zoneinfo import ZoneInfo

from cloud.store_tasks import clickup_import as ci, model, service as svc, store as st

log = logging.getLogger("store_tasks")

EVENTS = ["taskCreated", "taskUpdated", "taskDeleted", "taskStatusUpdated", "taskAssigneeUpdated",
          "taskDueDateUpdated", "taskMoved", "taskCommentPosted", "taskCommentUpdated"]
DRAIN_SECONDS = 60
DRAIN_BATCH = 50


def endpoint(root: str, store_id: str, list_id: str) -> str:
    return f"{root.rstrip('/')}/webhooks/clickup/{store_id}/{list_id}"


def hook(tasks: svc.Tasks, store_id: str, list_id: str) -> dict | None:
    return ((tasks.docs.get(st.WEBHOOKS, store_id) or {}).get("hooks") or {}).get(list_id)


def signed(secret: str, body: bytes, signature: str) -> bool:
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(want, signature)


def ensure(tasks: svc.Tasks, store_id: str, root: str, get=ci._get, send=ci._send) -> dict:
    """One healthy webhook per configured list, pointing at this API. Safe to run any time."""
    cfg = tasks.store(store_id)
    team = cfg.clickup["team_id"]
    live = {str(w["id"]): w for w in get(f"/team/{team}/webhook").get("webhooks") or []}
    hooks = dict((tasks.docs.get(st.WEBHOOKS, store_id) or {}).get("hooks") or {})
    out = {"kept": 0, "created": 0}
    for list_id in cfg.clickup["lists"]:
        url = endpoint(root, store_id, list_id)
        cur = hooks.get(list_id)
        w = live.get(str(cur["id"])) if cur else None
        if w and w.get("endpoint") == url and (w.get("health") or {}).get("status", "active") == "active":
            out["kept"] += 1
            continue
        if w:
            send("DELETE", f"/webhook/{w['id']}")
        made = send("POST", f"/team/{team}/webhook", {"endpoint": url, "events": EVENTS, "list_id": int(list_id)})
        made = made.get("webhook") or made
        hooks[list_id] = {"id": str(made["id"]), "secret": made["secret"], "at": tasks.now().isoformat()}
        tasks.docs.set(st.WEBHOOKS, store_id, {"id": store_id, "hooks": hooks}, merge=True)
        out["created"] += 1
    return out


def _upsert(tasks: svc.Tasks, cfg: model.StoreConfig, shift: str, raw: dict, get) -> str | None:
    """Write one ClickUp task the way the mirror would. Returns the task doc id to copy files for."""
    if ci.is_form(raw):
        c = ci.to_count(cfg, raw)
        prev = tasks.docs.get(st.COUNTS, c["id"])
        if prev and prev.get("source") == "clickup" and prev.get("clickup_id") != c["clickup_id"]:
            if prev["submitted_at"] > c["submitted_at"]:
                return None  # an older submission that day changed: the newer one stays
            c["revisions"] = (prev.get("revisions") or []) + [
                {k: prev.get(k) for k in ("items", "notes", "tips", "submitted_by", "submitted_at")}]
        ci.write_counts(tasks, [c])
        return None
    parent = get(f"/task/{raw['parent']}") if raw.get("parent") else None
    tz = ZoneInfo(cfg.tz)
    undated = not (raw.get("start_date") or raw.get("due_date"))
    doc = ci.to_task(cfg, shift, raw, ci.task_day(parent, tz) if (parent and undated) else None)
    # Steps on a months-old parent point at that day's copy of it, as in the mirror.
    day = [d for d in tasks.docs.query(st.TASKS, store=cfg.id, date=doc["date"], source="clickup")
           if d["id"] != doc["id"]]
    extra = [ci.to_task(cfg, shift, parent)] if parent else []
    ci._reparent(day + extra + [doc])
    ci.write_tasks(tasks, cfg, [doc])
    return doc["id"]


def receive(tasks: svc.Tasks, store_id: str, list_id: str, payload: dict, get=None) -> dict:
    """Apply one webhook call. Anything that fails is retried from the inbox by the next tick."""
    get = get or ci._get
    cfg = tasks.store(store_id)
    shift = cfg.clickup["lists"].get(list_id)
    task_id = str(payload.get("task_id") or "")
    if not (shift and task_id):
        return {"ignored": True}
    doc_id = f"cu_{task_id}"
    if payload.get("event") == "taskDeleted":
        cur = tasks.docs.get(st.TASKS, doc_id)
        if cur and cur.get("source") == "clickup" and not cfg.generated(datetime.date.fromisoformat(cur["date"])):
            tasks.docs.delete(st.TASKS, doc_id)
        return {"deleted": bool(cur)}
    entry = {"id": f"{store_id}_{task_id}", "store": store_id, "list_id": list_id, "task_id": task_id,
             "event": payload.get("event"), "at": tasks.now().isoformat(), "fields": True}
    try:
        entry["doc"] = _upsert(tasks, cfg, shift, get(f"/task/{task_id}?include_markdown_description=true"), get)
        entry["fields"] = False
    except Exception as e:
        log.error("store-tasks fail reason=webhook_upsert store=%s task=%s err=%r", store_id, task_id, e)
    if entry["fields"] or entry.get("doc"):
        tasks.docs.set(st.INBOX, entry["id"], entry)
    return {"task": doc_id, "queued": entry["fields"] or bool(entry.get("doc"))}


def drain(tasks: svc.Tasks, store_id: str, get=ci._get, download=ci._download) -> dict:
    """Finish what webhook calls left: fields that failed, then comments and files."""
    cfg = tasks.store(store_id)
    todo = sorted(tasks.docs.query(st.INBOX, store=store_id), key=lambda e: e["at"])[:DRAIN_BATCH]
    if not todo:
        return {}
    deadline = time.time() + DRAIN_SECONDS
    for e in todo:
        if e.get("fields"):
            try:
                raw = get(f"/task/{e['task_id']}?include_markdown_description=true")
                e["doc"] = _upsert(tasks, cfg, cfg.clickup["lists"][e["list_id"]], raw, get)
                e["fields"] = False
            except Exception as err:
                log.error("store-tasks fail reason=webhook_retry store=%s task=%s err=%r", store_id, e["task_id"], err)
    docs = list(dict.fromkeys(e["doc"] for e in todo if e.get("doc") and not e.get("fields")))
    started = tasks.now().isoformat()
    files = ci.import_task_files(tasks, cfg, get, download, only=docs, deadline=deadline,
                                 max_bytes=ci.MIRROR_MAX_FILE_BYTES) if docs else {}
    copied = {d for d in docs if ((tasks.docs.get(st.TASKS, d) or {}).get("clickup_full_at") or "") >= started}
    done = [e for e in todo if not e.get("fields") and (not e.get("doc") or e["doc"] in copied)]
    for e in done:
        tasks.docs.delete(st.INBOX, e["id"])
    return {"done": len(done), "left": len(todo) - len(done), **({"files": files} if files else {})}
