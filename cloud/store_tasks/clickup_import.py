"""ClickUp → Store Tasks: one-time backfill and the nightly mirror until cutover.

Read-only against ClickUp. Every write is keyed by the ClickUp task id (``cu_<id>``), so
re-running is an upsert.

    python3 -m cloud.store_tasks.clickup_import --store palmetto --dry-run
    python3 -m cloud.store_tasks.clickup_import --store palmetto --all
    python3 -m cloud.store_tasks.clickup_import --store palmetto --mirror
    python3 -m cloud.store_tasks.clickup_import --store palmetto --files   # everything on each task (resumable)
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import dataclasses
import datetime
import hashlib
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from cloud.store_tasks import auth, model, service as svc, store as st

API = "https://api.clickup.com/api/v2"
TEMPLATE_WINDOW_DAYS = 21
MIRROR_LOOKBACK_DAYS = 3
MIRROR_FILES_SECONDS = 90  # the scheduled tick has 300 s, and fetching the lists takes up to ~100; the rest waits for the next run
MIRROR_MAX_FILE_BYTES = 100 * 1024 * 1024  # larger videos stay in ClickUp until the laptop backfill copies them
FORM_TAG = "closing submission"
PHOTO_TAG = "needs photo submission"
DATE_ONLY_HOUR = 4  # ClickUp stores a date with no time as 04:00 local


def _get(path: str, pat: str | None = None):
    pat = pat or os.environ.get("CLICKUP_PAT", "")
    if not pat:
        raise RuntimeError("CLICKUP_PAT is not set")
    for attempt in range(8):
        try:
            req = urllib.request.Request(API + path, headers={"Authorization": pat})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(15)
                continue
            raise RuntimeError(f"ClickUp GET {path.split('?')[0]} → HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError):
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"ClickUp GET {path.split('?')[0]} kept failing")


def _send(method: str, path: str, body: dict | None = None, pat: str | None = None) -> dict:
    """A ClickUp write (webhook registration). Not retried: the caller re-checks state next run."""
    pat = pat or os.environ.get("CLICKUP_PAT", "")
    if not pat:
        raise RuntimeError("CLICKUP_PAT is not set")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Authorization": pat, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ClickUp {method} {path} → HTTP {e.code}") from None


def fetch_list(list_id: str, updated_since_ms: int | None = None, get=_get) -> list[dict]:
    out, page = [], 0
    while True:
        q = {"page": page, "include_closed": "true", "subtasks": "true", "archived": "false"}
        if updated_since_ms:
            q["date_updated_gt"] = updated_since_ms
        r = get(f"/list/{list_id}/task?{urllib.parse.urlencode(q)}")
        out += r["tasks"]
        if r.get("last_page") or not r["tasks"]:
            return out
        page += 1


def fetch_members(team_id: str, get=_get) -> list[dict]:
    for team in get("/team")["teams"]:
        if team["id"] == team_id:
            return [m["user"] for m in team["members"]]
    raise RuntimeError(f"ClickUp workspace {team_id} not visible to this token")


# ── mapping ────────────────────────────────────────────────────────────────


def _local(ms, tz: ZoneInfo) -> datetime.datetime | None:
    return datetime.datetime.fromtimestamp(int(ms) / 1000, tz) if ms else None


def _field(task: dict, name: str):
    for f in task.get("custom_fields") or []:
        if f.get("name") == name:
            return f.get("value")
    return None


def _tags(task: dict) -> set[str]:
    return {t["name"] for t in task.get("tags") or []}


def is_form(task: dict) -> bool:
    return FORM_TAG in _tags(task) or (task.get("name") or "").startswith("Form Submission")


def staff_id(clickup_user_id) -> str:
    return f"cu{clickup_user_id}"


def task_day(task: dict, tz: ZoneInfo) -> datetime.date:
    anchor = task.get("start_date") or task.get("due_date") or task.get("date_created")
    return _local(anchor, tz).date()


def to_task(cfg: model.StoreConfig, shift_id: str, task: dict, day: datetime.date | None = None) -> dict:
    tz = ZoneInfo(cfg.tz)
    day = day or task_day(task, tz)
    due = _local(task.get("due_date"), tz)
    if due is None or due.date() != day or (due.hour == DATE_ONLY_HOUR and due.minute == 0):
        due_at = model.due_at(cfg, shift_id, day)
    else:
        due_at = due.isoformat()
    closed = task.get("date_closed") or task.get("date_done")
    done = (task.get("status") or {}).get("type") == "closed" or bool(closed)
    assignee = (task.get("assignees") or [None])[0]
    order = _field(task, "Order #")
    try:
        order = float(order) if order not in (None, "") else 900 + float(task.get("orderindex") or 0) / 1e9
    except ValueError:
        order = 999.0
    return {
        "id": f"cu_{task['id']}", "store": cfg.id, "date": day.isoformat(), "shift": shift_id,
        "title": (task.get("name") or "").strip(), "instructions": (task.get("text_content") or "").strip(),
        "order": order, "template_id": None,
        "parent_id": f"cu_{task['parent']}" if task.get("parent") else None,
        "needs_photo": PHOTO_TAG in _tags(task), "due_at": due_at,
        "status": model.DONE if done else model.OPEN,
        "assignee_id": staff_id(assignee["id"]) if assignee else None,
        "completed_by": staff_id(assignee["id"]) if (done and assignee) else None,
        "completed_at": _local(closed, tz).isoformat() if (done and closed) else None,
        "source": "clickup", "clickup_id": task["id"],
        "clickup_url": task.get("url"),
        "created_at": _local(task.get("date_created"), tz).isoformat(),
        "updated_at": _local(task.get("date_updated") or task.get("date_created"), tz).isoformat(),
    }


def to_count(cfg: model.StoreConfig, task: dict) -> dict:
    tz = ZoneInfo(cfg.tz)
    known = {i["id"] for i in cfg.count_items}
    items, notes = {}, {"shift": "", "low": "", "out": ""}
    for f in task.get("custom_fields") or []:
        v = f.get("value")
        if v in (None, "", []):
            continue
        name = f["name"]
        if f["id"] in known:
            items[f["id"]] = str(v).strip()
        elif name == "Shift notes":
            notes["shift"] = str(v).strip()
        elif name == "Manual LOW notes":
            notes["low"] = str(v).strip()
        elif name == "Manual OUT notes":
            notes["out"] = str(v).strip()
    created = _local(task["date_created"], tz)
    return {
        "id": f"{cfg.id}_{created.date().isoformat()}", "store": cfg.id,
        "date": created.date().isoformat(), "items": items, "notes": notes, "tips": _field(task, "Tips"),
        "submitted_by": staff_id(task["creator"]["id"]) if (task.get("creator") or {}).get("id") else None,
        "submitted_at": created.isoformat(), "source": "clickup",
        "clickup_id": task["id"], "revisions": [],
    }


NOTE_FIELDS = ("Shift notes", "Manual LOW notes", "Manual OUT notes", "Summary", "Tips")


def count_items(form_tasks: list[dict]) -> list[dict]:
    """Closing-count fields, in form order, from the fields submissions actually fill in."""
    seen: dict[str, dict] = {}
    for t in form_tasks:
        for f in t.get("custom_fields") or []:
            if (f.get("type") in ("short_text", "number") and f["name"] not in NOTE_FIELDS
                    and f["name"] != "Order #" and f.get("value") not in (None, "")):
                seen.setdefault(f["id"], {"id": f["id"], "name": f["name"],
                                          "kind": "number" if f["type"] == "number" else "text"})
    return sorted(seen.values(), key=lambda i: i["name"].lower())


_WEEKDAY = re.compile(r"^\s*(mon|tue|wed|thu|fri|sat|sun)[a-z]*\s*:", re.IGNORECASE)
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def named_weekday(title: str) -> tuple[int, ...]:
    """'Tues: Restock…' → (1,). ClickUp lists every weekday's deep-clean item every day."""
    m = _WEEKDAY.match(title or "")
    return (_WEEKDAYS.index(m.group(1).lower()),) if m else ()


def _norm(title: str) -> str:
    return " ".join((title or "").split()).lower()


def template_id(shift: str, parent_title: str | None, title: str) -> str:
    key = f"{shift}|{_norm(parent_title or '')}|{_norm(title)}"
    return "t" + hashlib.sha1(key.encode()).hexdigest()[:10]


def infer_templates(cfg: model.StoreConfig, tasks: list[dict], today: datetime.date) -> list[model.Template]:
    """Task definitions from what ClickUp actually scheduled over the last three weeks.

    ``tasks`` are ``to_task`` documents. A title seen on most days is daily; one seen on the
    same weekday(s) each week gets those weekdays; anything seen once is a one-off and dropped.
    """
    lo = today - datetime.timedelta(days=TEMPLATE_WINDOW_DAYS)
    recent = [t for t in tasks if lo.isoformat() <= t["date"] <= today.isoformat() and not t.get("stale_parent")]
    titles = {t["id"]: t["title"] for t in recent}
    days_by_shift: dict[str, set[str]] = collections.defaultdict(set)
    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for t in recent:
        if t["parent_id"] and t["parent_id"] not in titles:
            continue
        days_by_shift[t["shift"]].add(t["date"])
        parent_title = titles.get(t["parent_id"]) if t["parent_id"] else None
        groups[(t["shift"], _norm(parent_title or ""), _norm(t["title"]))].append(t)

    out = []
    for (shift, parent_norm, _), rows in groups.items():
        dates = {r["date"] for r in rows}
        all_days = days_by_shift[shift]
        if len(dates) < 2:
            continue
        weekdays = named_weekday(rows[0]["title"])
        if not weekdays and len(dates) < 0.7 * len(all_days):
            per_wd = collections.Counter(datetime.date.fromisoformat(d).weekday() for d in dates)
            all_wd = collections.Counter(datetime.date.fromisoformat(d).weekday() for d in all_days)
            weekdays = tuple(sorted(w for w, n in per_wd.items() if n >= 0.5 * all_wd[w] and n >= 2))
            if not weekdays:
                continue
        latest = max(rows, key=lambda r: r["date"])
        parent_title = titles.get(latest["parent_id"]) if latest["parent_id"] else None
        out.append(model.Template(
            id=template_id(shift, parent_title, latest["title"]), store=cfg.id, shift=shift,
            title=latest["title"],
            instructions=next((r["instructions"] for r in sorted(rows, key=lambda r: r["date"], reverse=True)
                               if r["instructions"]), ""),
            order=statistics.median(r["order"] for r in rows), weekdays=weekdays,
            parent_id=template_id(shift, None, parent_title) if parent_norm else None,
            needs_photo=any(r["needs_photo"] for r in rows),
        ))
    ids = {t.id for t in out}
    return sorted((t for t in out if not t.parent_id or t.parent_id in ids),
                  key=lambda t: (t.shift, t.order, t.title))


# ── import steps ───────────────────────────────────────────────────────────


def import_staff(tasks: svc.Tasks, cfg: model.StoreConfig, get=_get) -> int:
    n = 0
    for u in fetch_members(cfg.clickup["team_id"], get):
        sid = staff_id(u["id"])
        cur = tasks.docs.get(st.STAFF, sid) or {}
        tasks.docs.set(st.STAFF, sid, {
            **cur, "id": sid, "store": cfg.id, "name": (u.get("username") or u.get("email") or sid).strip(),
            "role": cur.get("role") or (auth.MANAGER if u.get("role") in (1, 2) else auth.CREW),
            "active": cur.get("active", True), "clickup_user_id": str(u["id"]),
            "adp_name": cur.get("adp_name", ""),
        })
        n += 1
    return n


def fetch_all(cfg: model.StoreConfig, updated_since_ms: int | None = None, get=_get) -> dict[str, list[dict]]:
    """{shift_id: [raw ClickUp task, …]} for every configured list."""
    return {shift: fetch_list(list_id, updated_since_ms, get) for list_id, shift in cfg.clickup["lists"].items()}


def split(cfg: model.StoreConfig, raw: dict[str, list[dict]]) -> tuple[list[dict], list[dict]]:
    """Raw ClickUp tasks → (task docs, closing-count docs). The latest submission per day wins."""
    tz = ZoneInfo(cfg.tz)
    by_id = {r["id"]: r for rows in raw.values() for r in rows}
    docs, counts = [], {}
    for shift, rows in raw.items():
        for r in rows:
            if is_form(r):
                c = to_count(cfg, r)
                prev = counts.get(c["id"])
                if prev is None or c["submitted_at"] > prev["submitted_at"]:
                    if prev:
                        c["revisions"] = prev["revisions"] + [
                            {k: prev[k] for k in ("items", "notes", "tips", "submitted_by", "submitted_at")}]
                    counts[c["id"]] = c
                elif prev:
                    prev["revisions"].append(
                        {k: c[k] for k in ("items", "notes", "tips", "submitted_by", "submitted_at")})
            else:
                # An undated subtask belongs to its parent's day, not the day ClickUp created it.
                parent = by_id.get(r.get("parent"))
                undated = not (r.get("start_date") or r.get("due_date"))
                docs.append(to_task(cfg, shift, r, task_day(parent, tz) if (parent and undated) else None))
    _reparent(docs)
    return docs, list(counts.values())


def _reparent(docs: list[dict]) -> None:
    """Recurring subtasks can hang off a months-old parent; point them at that day's copy of it."""
    by_id = {d["id"]: d for d in docs}
    todays = {(d["date"], d["shift"], _norm(d["title"])): d["id"] for d in docs if not d["parent_id"]}
    for d in docs:
        parent = by_id.get(d["parent_id"]) if d["parent_id"] else None
        if d["parent_id"] and (parent is None or parent["date"] != d["date"]):
            d["parent_id"] = todays.get((d["date"], d["shift"], _norm(parent["title"]))) if parent else None
            # Its parent is not on that day's list, so nobody sees it there: history, not a task to carry over.
            d["stale_parent"] = d["parent_id"] is None


def write_tasks(tasks: svc.Tasks, cfg: model.StoreConfig, docs: list[dict]) -> int:
    """Upsert mirrored tasks. Days this service generates are never overwritten by ClickUp."""
    keep = [d for d in docs if not cfg.generated(datetime.date.fromisoformat(d["date"]))]
    return tasks.docs.set_many(st.TASKS, keep, merge=True)


def import_photos(tasks: svc.Tasks, cfg: model.StoreConfig, raw: dict[str, list[dict]]) -> int:
    """Copy the deep-clean photos uploaded with closing submissions into our bucket, once each."""
    tz = ZoneInfo(cfg.tz)
    n = 0
    for rows in raw.values():
        for r in rows:
            files = [a for f in r.get("custom_fields") or [] if f.get("type") == "attachment"
                     for a in (f.get("value") or [])]
            if not files or not is_form(r):
                continue
            day = _local(r["date_created"], tz).date().isoformat()
            doc_id = f"{cfg.id}_{day}"
            cur = tasks.docs.get(st.COUNTS, doc_id)
            if cur is None:
                continue
            photos = list(cur.get("photos") or [])
            have = {p.get("clickup_attachment_id") for p in photos}
            for a in files:
                if a.get("id") in have or not a.get("url"):
                    continue
                try:
                    with urllib.request.urlopen(a["url"], timeout=60) as resp:
                        data = resp.read()
                        ctype = resp.headers.get_content_type()
                except (urllib.error.URLError, TimeoutError) as e:
                    print(f"store-tasks fail reason=photo_download task={r['id']} err={e!r}", file=sys.stderr)
                    continue
                path = f"{cfg.id}/{day}/count/{a['id']}"
                tasks.photos.put(path, data, ctype)
                photos.append({"path": path, "by": None, "at": _local(a.get("date"), tz).isoformat(),
                               "clickup_attachment_id": a["id"]})
                n += 1
            if len(photos) != len(cur.get("photos") or []):
                tasks.docs.set(st.COUNTS, doc_id, {"photos": photos}, merge=True)
    return n


def _download(url: str) -> tuple[bytes, str]:
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read(), resp.headers.get_content_type()


COMMENT_PAGE = 25  # ClickUp returns a task's newest 25 comments per call


def _comments(path: str, get=_get) -> list[dict]:
    """Every comment, oldest pages included, each with its replies under ``replies``."""
    out: list[dict] = []
    query = ""
    while True:
        page = get(f"{path}{query}").get("comments") or []
        out += page
        if len(page) < COMMENT_PAGE:
            break
        last = page[-1]
        query = f"?start={last['date']}&start_id={last['id']}"
    for c in out:
        c["replies"] = get(f"/comment/{c['id']}/reply").get("comments") or [] if c.get("reply_count") else []
    return out


def _extras(task_id: str, get=_get) -> tuple[dict, list[dict]]:
    """A task in full (attachments, checklists, every field) and its comments: the list download has neither."""
    return get(f"/task/{task_id}?include_markdown_description=true"), _comments(f"/task/{task_id}/comment", get)


def _comment_files(c: dict) -> list[dict]:
    """Images and files pasted into a comment, as attachment-shaped dicts."""
    out = []
    for block in c.get("comment") or []:
        for key in ("image", "attachment"):
            obj = block.get(key)
            if isinstance(obj, dict) and obj.get("url"):
                out.append({"id": f"{c['id']}-{obj.get('id') or len(out)}", "url": obj["url"],
                            "mimetype": obj.get("mimetype") or ("image/jpeg" if key == "image" else ""),
                            "title": obj.get("title") or obj.get("name") or "", "size": obj.get("size"),
                            "date": c.get("date"), "user": c.get("user"), "comment_id": f"cu{c['id']}"})
    return out


def _note(c: dict, tz: ZoneInfo, reply_to: str | None = None) -> dict:
    return {"id": f"cu{c['id']}", "by": staff_id(c["user"]["id"]) if c.get("user") else None,
            "at": _local(c.get("date"), tz).isoformat(), "text": (c.get("comment_text") or "").strip(),
            "reply_to": reply_to}


def _meta(t: dict) -> dict:
    """ClickUp fields the app does not act on, kept on the task (the raw archive keeps the rest)."""
    fields = {f["name"]: f.get("value") for f in t.get("custom_fields") or [] if f.get("value") not in (None, "", [])}
    return {"tags": sorted(_tags(t)), "priority": (t.get("priority") or {}).get("priority"),
            "creator": (t.get("creator") or {}).get("username"), "status": (t.get("status") or {}).get("status"),
            "start_date": t.get("start_date"), "time_estimate": t.get("time_estimate"),
            "custom_fields": {k: v for k, v in fields.items() if not isinstance(v, (list, dict))},
            "markdown_description": t.get("markdown_description") or ""}


def archive_path(store_id: str, *parts: str) -> str:
    """Raw ClickUp copies sit outside the store's photo prefix, so the app never serves them."""
    return "/".join(("archive", store_id, "clickup", *parts))


def import_task_files(tasks: svc.Tasks, cfg: model.StoreConfig, get=_get, download=_download,
                      workers: int = 6, only: list[str] | None = None, deadline: float | None = None,
                      max_bytes: int | None = None) -> dict:
    """Copy everything ClickUp holds on each mirrored task: files (into our bucket), comments with
    replies, checklists and other fields, plus the raw task and comments as JSON. Resumable.

    ``only``: these task ids, copied again even if done before (the mirror passes what changed).
    ``deadline`` (epoch seconds): stop starting new tasks after it."""
    tz = ZoneInfo(cfg.tz)
    if only is not None:
        todo = [d for d in (tasks.docs.get(st.TASKS, i) for i in only) if d and d.get("source") == "clickup"]
    else:
        todo = [d for d in tasks.docs.query(st.TASKS, store=cfg.id, source="clickup") if not d.get("clickup_full_at")]
    # ClickUp allows ~100 calls a minute, so a full run takes hours: photo tasks first, then newest first.
    todo.sort(key=lambda d: d["date"], reverse=True)
    todo.sort(key=lambda d: not d.get("needs_photo"))
    out = collections.Counter(tasks=len(todo))
    stamp = tasks.now().isoformat()
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        jobs = {pool.submit(_extras, d["clickup_id"], get): d for d in todo}
        for i, job in enumerate(concurrent.futures.as_completed(jobs), 1):
            d = jobs[job]
            if deadline and time.time() > deadline:
                pool.shutdown(wait=False, cancel_futures=True)
                out["deferred"] = len(todo) - i + 1
                break
            try:
                full, comments = job.result()
            except RuntimeError as e:
                print(f"store-tasks fail reason=clickup_files task={d['id']} err={e}", file=sys.stderr)
                out["failed"] += 1
                continue
            tasks.photos.put(archive_path(cfg.id, "tasks", f"{d['clickup_id']}.json"),
                             json.dumps({"task": full, "comments": comments}).encode(), "application/json")
            photos, files = list(d.get("photos") or []), list(d.get("files") or [])
            have = {p.get("clickup_attachment_id") for p in photos + files}
            every = [a for a in full.get("attachments") or [] if not a.get("deleted")]
            every += [f for c in comments for r in [c, *c["replies"]] for f in _comment_files(r)]
            for a in every:
                if a.get("id") in have:
                    continue
                if max_bytes and int(a.get("size") or 0) > max_bytes:
                    print(f"store-tasks fail reason=file_too_big task={d['id']} bytes={a.get('size')}", file=sys.stderr)
                    out["too_big"] += 1
                    continue
                try:
                    data, ctype = download(a["url"])
                except (urllib.error.URLError, TimeoutError, ValueError) as e:
                    print(f"store-tasks fail reason=file_download task={d['id']} err={e!r}", file=sys.stderr)
                    out["file_failed"] += 1
                    continue
                ctype = a.get("mimetype") or ctype
                safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", a["id"])
                path = f"{cfg.id}/{d['date']}/{d['id']}/{safe_id}"
                tasks.photos.put(path, data, ctype)
                entry = {"path": path, "by": staff_id(a["user"]["id"]) if a.get("user") else None,
                         "at": _local(a.get("date"), tz).isoformat() if a.get("date") else None,
                         "clickup_attachment_id": a["id"]}
                if a.get("comment_id"):
                    entry["comment_id"] = a["comment_id"]
                if ctype.startswith("image/"):
                    photos.append(entry)
                    out["photos"] += 1
                else:
                    files.append({**entry, "name": a.get("title") or safe_id, "type": ctype, "size": len(data)})
                    out["files"] += 1
            known = {c["id"] for c in d.get("comments") or []}
            notes = [n for c in comments for n in [_note(c, tz)] + [_note(r, tz, f"cu{c['id']}") for r in c["replies"]]
                     if n["id"] not in known and n["text"]]
            out["comments"] += len(notes)
            checklists = [{"name": c.get("name") or "Checklist",
                           "items": [{"name": it.get("name") or "", "done": bool(it.get("resolved"))}
                                     for it in c.get("items") or []]}
                          for c in full.get("checklists") or [] if c.get("items")]
            out["checklists"] += len(checklists)
            tasks.docs.set(st.TASKS, d["id"], {
                "photos": photos, "files": files, "checklists": checklists, "clickup": _meta(full),
                "comments": sorted((d.get("comments") or []) + notes, key=lambda c: c["at"]),
                "clickup_full_at": stamp}, merge=True)
            if i % 500 == 0:
                print(f"clickup files: {i}/{len(todo)} {dict(out)}", file=sys.stderr, flush=True)
    return dict(out)


def seed_recent_photos(tasks: svc.Tasks, cfg: model.StoreConfig, docs: list[dict] | None = None,
                       titles: dict[str, str] | None = None) -> int:
    """Give each recurring task the last few photos ClickUp holds for it ("photos from earlier days").
    ``docs``: only these tasks (the mirror passes what changed); ``titles`` names their parents."""
    docs = tasks.docs.query(st.TASKS, store=cfg.id, source="clickup") if docs is None else docs
    titles = {**(titles or {}), **{d["id"]: d["title"] for d in docs}}
    by_template: dict[str, list[dict]] = collections.defaultdict(list)
    for d in docs:
        parent = titles.get(d["parent_id"]) if d.get("parent_id") else None
        tid = template_id(d["shift"], parent, d["title"])
        by_template[tid] += [{**p, "date": d["date"]} for p in d.get("photos") or []]
    n = 0
    for tpl in tasks.templates(cfg.id):
        found = by_template.get(tpl.id)
        if not found:
            continue
        cur = tasks.docs.get(st.TEMPLATES, tpl.id) or {}
        merged = {p["path"]: p for p in found + (cur.get("recent_photos") or [])}
        recent = sorted(merged.values(), key=lambda p: (p.get("date", ""), p.get("at") or ""), reverse=True)
        tasks.docs.set(st.TEMPLATES, tpl.id, {"recent_photos": recent[:svc.RECENT_PHOTOS]}, merge=True)
        n += 1
    return n


ASKS_FOR_PHOTO = re.compile(r"\b(attach|upload|take|send|add)\s+(a\s+)?(photo|picture|pic)s?\b", re.I)
PHOTO_DAYS = 3


def photo_rules(tasks: svc.Tasks, store_id: str, min_days: int = PHOTO_DAYS) -> list[dict]:
    """Recurring tasks that should need a photo: the how-to asks for one, or ClickUp holds photos
    for it on ``min_days`` or more days. A task with steps is left alone; its steps carry the rule."""
    docs = tasks.docs.query(st.TASKS, store=store_id, source="clickup")
    titles = {d["id"]: d["title"] for d in docs}
    days: dict[str, set[str]] = collections.defaultdict(set)
    for d in docs:
        if d.get("photos"):
            parent = titles.get(d["parent_id"]) if d.get("parent_id") else None
            days[template_id(d["shift"], parent, d["title"])].add(d["date"])
    templates = [t for t in tasks.templates(store_id) if t.active]
    parents = {t.parent_id for t in templates if t.parent_id}
    out = []
    for t in templates:
        asks = bool(ASKS_FOR_PHOTO.search(f"{t.title} {t.instructions}"))
        seen = days.get(t.id, set())
        if t.id in parents or not (asks or len(seen) >= min_days):
            continue
        out.append({"id": t.id, "shift": t.shift, "title": t.title, "needs_photo": t.needs_photo,
                    "asks": asks, "photo_days": len(seen), "last_photo": max(seen, default=None)})
    return sorted(out, key=lambda r: (r["shift"], -r["photo_days"]))


def write_counts(tasks: svc.Tasks, counts: list[dict]) -> int:
    n = 0
    for c in counts:
        cur = tasks.docs.get(st.COUNTS, c["id"])
        if cur and cur.get("source") == "app":
            continue  # a count entered in the app is never replaced by ClickUp
        tasks.docs.set(st.COUNTS, c["id"], {**c, "photos": (cur or {}).get("photos") or []})
        n += 1
    return n


def write_templates(tasks: svc.Tasks, templates: list[model.Template]) -> int:
    stamp = tasks.now().isoformat()
    n = 0
    for t in templates:
        if tasks.docs.get(st.TEMPLATES, t.id) is None:  # never clobber a manager's edit
            tasks.docs.set(st.TEMPLATES, t.id, {**t.to_dict(), "updated_at": stamp, "updated_by": "clickup-import"})
            n += 1
    return n


def _stale(doc: dict, stored: dict | None) -> bool:
    """ClickUp changed the task (a comment, a photo, a tick) after its files and comments were copied."""
    copied = (stored or {}).get("clickup_full_at")
    return not copied or datetime.datetime.fromisoformat(doc["updated_at"]) > datetime.datetime.fromisoformat(copied)


def mirror(tasks: svc.Tasks, store_id: str, get=_get, download=_download) -> dict:
    """Re-sync what changed in ClickUp recently: fields and status, then comments, photos and files.
    Safe to run any time; ClickUp days from the cutover date on are left alone."""
    cfg = tasks.store(store_id)
    since = int((tasks.now() - datetime.timedelta(days=MIRROR_LOOKBACK_DAYS)).timestamp() * 1000)
    raw = fetch_all(cfg, since, get)
    docs, counts = split(cfg, raw)
    mirrored = [d for d in docs if not cfg.generated(datetime.date.fromisoformat(d["date"]))]
    changed = [d["id"] for d in mirrored if _stale(d, tasks.docs.get(st.TASKS, d["id"]))]
    out = {"store": store_id, "tasks": write_tasks(tasks, cfg, docs), "counts": write_counts(tasks, counts),
           "staff": import_staff(tasks, cfg, get)}
    if changed:
        out["files"] = import_task_files(tasks, cfg, get, download, only=changed,
                                         deadline=time.time() + MIRROR_FILES_SECONDS, max_bytes=MIRROR_MAX_FILE_BYTES)
        if out["files"].get("photos"):
            out["files"]["earlier_photos"] = seed_recent_photos(
                tasks, cfg, [tasks.docs.get(st.TASKS, i) for i in changed], {d["id"]: d["title"] for d in docs})
    return out


def summarize(docs: list[dict]) -> dict:
    out: dict[str, dict[str, int]] = {}
    for d in docs:
        s = out.setdefault(d["shift"], {"open": 0, "done": 0})
        s[d["status"]] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", required=True)
    ap.add_argument("--dry-run", action="store_true", help="fetch and report; write nothing")
    ap.add_argument("--all", action="store_true", help="staff, templates, history, counts, photos")
    ap.add_argument("--mirror", action="store_true", help="only what changed in the last few days")
    ap.add_argument("--photos-only", action="store_true", help="copy photos for submissions already stored")
    ap.add_argument("--files", action="store_true", help="everything on each task already stored")
    args = ap.parse_args(argv)

    tasks = svc.from_env()
    cfg = tasks.store(args.store)
    if args.files:
        report = import_task_files(tasks, cfg)
        report["templates_with_earlier_photos"] = seed_recent_photos(tasks, cfg)
        print(json.dumps({"store": args.store, **report}))
        return 0
    if args.mirror:
        print(json.dumps(mirror(tasks, args.store)))
        return 0
    raw = fetch_all(cfg)
    if args.photos_only:
        print(json.dumps({"store": args.store, "photos_copied": import_photos(tasks, cfg, raw)}))
        return 0
    if not cfg.count_items:
        forms = [r for rows in raw.values() for r in rows if is_form(r)]
        cfg = dataclasses.replace(cfg, count_items=tuple(count_items(forms)))
        if args.all and not args.dry_run:
            tasks.put_store(cfg)
    docs, counts = split(cfg, raw)
    templates = infer_templates(cfg, docs, cfg.today(tasks.now()))
    report = {"store": args.store, "tasks": len(docs), "by_shift": summarize(docs), "counts": len(counts),
              "templates": collections.Counter(t.shift for t in templates)}
    if args.dry_run or not args.all:
        print(json.dumps({**report, "dry_run": True}, default=dict))
        return 0
    report["staff_written"] = import_staff(tasks, cfg)
    report["tasks_written"] = write_tasks(tasks, cfg, docs)
    report["counts_written"] = write_counts(tasks, counts)
    report["templates_written"] = write_templates(tasks, templates)
    report["photos_copied"] = import_photos(tasks, cfg, raw)
    print(json.dumps(report, default=dict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
