"""Projects: private action-item lists for a few named people (the store leads), apart from the stores.

A project is not part of any store and has its own members, each known by their Google account:
a member signs in with Google and needs no staff record, PIN or store access, and a shop device
never sees a project. Items carry one or more owners, sub-points, store tags, an optional due date
and a thread of updates, and stay open until ticked. Meeting notes pasted in are split into items
per owner (``parse``) for the person to confirm.
"""

from __future__ import annotations

import datetime
import re
import uuid

from cloud.store_tasks import auth
from cloud.store_tasks import service as svc
from cloud.store_tasks import store as st

MAX_TITLE = 300
MAX_DETAIL = 4000
MAX_NOTE = 4000
MAX_ADDED = 100
EDITABLE = ("title", "detail", "owners", "tags", "due")

_MENTION = re.compile(r"\[@([^\]]+)\]\(#user_mention#(\d+)\)")
_NUMBERED = re.compile(r"^(\d+)[.)]\s+(.*)$")
_BULLET = re.compile(r"^[*\-•]\s+(.*)$")
_TAGS = re.compile(r"^\[([^\]]+)\]\s*")
_JOINERS = re.compile(r"\b(and)\b|[+/&,:;.\s]", re.I)


def _unescape(text: str) -> str:
    return re.sub(r"\\([\[\]+*_.\-()])", r"\1", text)


def parse(text: str, people: list[dict], tags: list[str], default_owner: str) -> list[dict]:
    """Meeting notes → draft items. A line of only names starts a group owned by those people; the
    numbered (or bulleted) lines under it are items and indented lines are an item's sub-points.
    A plain line is an item too, except the introduction above the first list."""
    by_mention = {p["id"].removeprefix("cu"): p["id"] for p in people}
    names = sorted(((n.lower(), p["id"]) for p in people for n in {p["name"], p["name"].split()[0]}),
                   key=lambda x: -len(x[0]))

    def owners_of(line: str) -> list[str] | None:
        """The people a line names, when it names people and nothing else."""
        found = [by_mention[m] for _, m in _MENTION.findall(line) if m in by_mention]
        rest = _MENTION.sub(" ", line).lower()
        for name, sid in names:
            hit = re.search(rf"(?<!\w)@?{re.escape(name)}(?!\w)", rest)
            if hit:
                rest = rest[:hit.start()] + " " + rest[hit.end():]
                found.append(sid)
        if not found or _JOINERS.sub("", rest):
            return None
        return list(dict.fromkeys(found))

    def clean(line: str) -> str:
        return _MENTION.sub(lambda m: m.group(1), line).strip()

    lines = [ln.expandtabs(4).rstrip() for ln in text.splitlines() if ln.strip()]
    listed = any(_NUMBERED.match(ln.strip()) or _BULLET.match(_unescape(ln.strip())) for ln in lines)
    out: list[dict] = []
    owners = [default_owner]
    started = False
    for ln in lines:
        indent = len(ln) - len(ln.lstrip())
        body = _unescape(ln.strip())
        if indent == 0 and (who := owners_of(body)) is not None:
            owners, started = who, True
            continue
        numbered, bullet = _NUMBERED.match(body), _BULLET.match(body)
        if indent > 0 and out:
            point = clean((numbered.group(2) if numbered else bullet.group(1) if bullet else body))
            depth = max(0, (indent - 1) // 4)
            out[-1]["detail"] = (out[-1]["detail"] + "\n" if out[-1]["detail"] else "") + "  " * depth + "- " + point
            continue
        if listed and not (numbered or bullet) and not started:
            continue  # the introduction above the first list
        started = True
        title = clean(numbered.group(2) if numbered else bullet.group(1) if bullet else body)
        found_tags: list[str] = []
        if (m := _TAGS.match(title)):
            wanted = [t for t in tags if re.search(rf"(?<!\w){re.escape(t)}(?!\w)", m.group(1), re.I)]
            if wanted:
                found_tags, title = wanted, title[m.end():]
        if title:
            out.append({"title": title[:MAX_TITLE], "detail": "", "owners": list(owners), "tags": found_tags, "due": None})
    return out[:MAX_ADDED]


class Tracker:
    def __init__(self, tasks: svc.Tasks) -> None:
        self.tasks = tasks
        self.docs = tasks.docs

    # ── projects and who is on them ─────────────────────────────────────────

    def projects(self, email: str) -> list[dict]:
        return [{"id": p["id"], "name": p["name"]} for p in self.docs.query(st.TRACKERS) if _member_id(p, email)]

    def member(self, pid: str, email: str) -> tuple[dict, str]:
        """The project and the member id behind a Google account. Anyone else is told nothing is there."""
        p = self.docs.get(st.TRACKERS, pid)
        mid = _member_id(p, email) if p else None
        if mid is None:
            raise svc.NotFound("project")
        return p, mid

    def create(self, pid: str, name: str, members: list[dict], admins: list[str], tags: list[str], by: str) -> dict:
        """Provisioning (not a route). ``members``: ``{"id", "name", "emails"}``; ``admins``: member ids."""
        cur = self.docs.get(st.TRACKERS, pid) or {}
        members = _members(members)
        if not admins or any(a not in {m["id"] for m in members} for a in admins):
            raise svc.Invalid("admins must be members")
        doc = {**cur, "id": pid, "name": name.strip(), "admins": admins, "members": members, "tags": tags,
               "history": (cur.get("history") or []) + [{"at": self.tasks.now().isoformat(), "by": by, "members": members}]}
        self.docs.set(st.TRACKERS, pid, doc)
        return doc

    def set_members(self, pid: str, email: str, wanted: list[dict]) -> tuple[dict, list[str], list[str]]:
        """Returns the project and the Google accounts added and removed, for the sign-in list."""
        p, me = self.member(pid, email)
        if me not in p["admins"]:
            raise auth.AuthError("only the person who runs this project can change who is on it", 403)
        for w in wanted:
            w.setdefault("id", f"m{uuid.uuid4().hex[:10]}")
        members = _members(wanted)
        kept = {m["id"] for m in members}
        members = [m for m in p["members"] if m["id"] in p["admins"] and m["id"] not in kept] + members
        before = {e for m in p["members"] for e in m["emails"]}
        after = {e for m in members for e in m["emails"]}
        p["members"] = members
        # Who could see the project, and when, is kept: each change is added, never replaced.
        p["history"] = (p.get("history") or []) + [{"at": self.tasks.now().isoformat(), "by": me, "members": members}]
        self.docs.set(st.TRACKERS, pid, p)
        return p, sorted(after - before), sorted(before - after)

    def view(self, pid: str, email: str) -> dict:
        p, me = self.member(pid, email)
        admin = me in p["admins"]
        items = sorted(self.docs.query(st.TRACKER_ITEMS, project=pid), key=lambda i: (i["order"], i["created_at"]))
        members = [{"id": m["id"], "name": m["name"], **({"emails": m["emails"]} if admin else {})} for m in p["members"]]
        return {"id": pid, "name": p["name"], "me": me, "admin": admin, "admins": p["admins"],
                "members": members, "tags": p.get("tags") or [], "items": items}

    # ── items ───────────────────────────────────────────────────────────────

    def _clean(self, p: dict, data: dict) -> dict:
        out: dict = {}
        if "title" in data:
            out["title"] = str(data["title"] or "").strip()[:MAX_TITLE]
            if not out["title"]:
                raise svc.Invalid("say what needs doing")
        if "detail" in data:
            out["detail"] = str(data["detail"] or "").strip()[:MAX_DETAIL]
        if "owners" in data:
            out["owners"] = list(dict.fromkeys(str(o) for o in data["owners"] or []))
            if any(o not in {m["id"] for m in p["members"]} for o in out["owners"]):
                raise svc.Invalid("an owner must be someone on this project")
        if "tags" in data:
            out["tags"] = [t for t in p.get("tags") or [] if t in (data["tags"] or [])]
        if "due" in data:
            out["due"] = datetime.date.fromisoformat(data["due"]).isoformat() if data["due"] else None
        return out

    def parse(self, pid: str, email: str, text: str) -> list[dict]:
        p, me = self.member(pid, email)
        return parse(text or "", p["members"], p.get("tags") or [], me)

    def add(self, pid: str, email: str, items: list[dict], at: str | None = None) -> list[dict]:
        p, me = self.member(pid, email)
        if not items or len(items) > MAX_ADDED:
            raise svc.Invalid(f"add between 1 and {MAX_ADDED} items at a time")
        stamp = at or self.tasks.now().isoformat()
        order = max((i["order"] for i in self.docs.query_fields(st.TRACKER_ITEMS, ["order"], project=pid)), default=0)
        made = []
        for n, data in enumerate(items, 1):
            fields = {"detail": "", "owners": [], "tags": [], "due": None, **self._clean(p, data)}
            if "title" not in fields:
                raise svc.Invalid("say what needs doing")
            made.append({"id": f"a{uuid.uuid4().hex[:12]}", "project": pid, **fields, "status": "open",
                         "order": order + n, "created_by": me, "created_at": stamp,
                         "done_by": None, "done_at": None, "notes": [], "log": []})
        self.docs.set_many(st.TRACKER_ITEMS, made)
        return made

    def _item(self, pid: str, email: str, item_id: str) -> tuple[dict, str, dict]:
        p, me = self.member(pid, email)
        item = self.docs.get(st.TRACKER_ITEMS, item_id)
        if item is None or item["project"] != pid:
            raise svc.NotFound("item")
        return p, me, item

    def update(self, pid: str, email: str, item_id: str, data: dict) -> dict:
        p, me, item = self._item(pid, email, item_id)
        stamp = self.tasks.now().isoformat()
        patch = self._clean(p, {k: data[k] for k in EDITABLE if k in data})
        if data.get("status") in ("open", "done") and data["status"] != item["status"]:
            done = data["status"] == "done"
            patch.update(status=data["status"], done_by=me if done else None, done_at=stamp if done else None)
        changed = {k: v for k, v in patch.items() if item.get(k) != v}
        if not changed:
            return item
        # What an item said before is kept beside it.
        entry = {"at": stamp, "by": me, "was": {k: item.get(k) for k in changed if k not in ("done_by", "done_at")}}
        item = {**item, **changed, "updated_at": stamp, "updated_by": me, "log": (item.get("log") or []) + [entry]}
        self.docs.set(st.TRACKER_ITEMS, item_id, item)
        return item

    def note(self, pid: str, email: str, item_id: str, text: str, at: str | None = None, by: str | None = None) -> dict:
        """``by`` is for importing someone else's earlier update; members post as themselves."""
        _, me, item = self._item(pid, email, item_id)
        text = (text or "").strip()[:MAX_NOTE]
        if not text:
            raise svc.Invalid("write an update")
        item["notes"] = (item.get("notes") or []) + [
            {"id": f"n{uuid.uuid4().hex[:10]}", "by": by or me, "at": at or self.tasks.now().isoformat(), "text": text}]
        self.docs.set(st.TRACKER_ITEMS, item_id, item)
        return item

    def remove(self, pid: str, email: str, item_id: str) -> None:
        p, me, item = self._item(pid, email, item_id)
        if me not in (item["created_by"], *p["admins"]):
            raise auth.AuthError("only the person who added it or who runs this project can delete it", 403)
        self.docs.delete(st.TRACKER_ITEMS, item_id)


def _member_id(p: dict, email: str) -> str | None:
    email = (email or "").strip().lower()
    return next((m["id"] for m in p.get("members", []) if email and email in m["emails"]), None)


def _members(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        name = str(r.get("name") or "").strip()
        emails = [e.strip().lower() for e in (r.get("emails") or [r.get("email") or ""]) if e and e.strip()]
        if not name:
            raise svc.Invalid("each person needs a name")
        if not emails or any("@" not in e or " " in e for e in emails):
            raise svc.Invalid(f"enter {name}'s Google account email")
        if seen & set(emails):
            raise svc.Invalid("two people have the same email")
        seen |= set(emails)
        out.append({"id": str(r["id"]), "name": name, "emails": emails})
    return out
