"""A private action-item tracker for a few named people (the store leads), beside the daily lists.

One board per store, with its own member list: only members see it, whatever their role, and a
shop device never does. Items carry one or more owners, sub-points, store tags, an optional due
date and a notes thread, and stay open until ticked. Meeting notes pasted in are split into
items per owner (``parse``) for the person to confirm.
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

    # ── the board and who is on it ──────────────────────────────────────────

    def board(self, store_id: str, staff: dict) -> dict:
        """The store's board, for a member. Anyone else is told nothing is there."""
        b = self.docs.get(st.TRACKERS, store_id)
        if b is None or staff["sid"] not in b.get("members", []):
            raise svc.NotFound("tracker")
        return b

    def create(self, store_id: str, name: str, admins: list[str], members: list[str], tags: list[str],
               by: str) -> dict:
        """Provisioning (not a route): the board, its admins and its first members."""
        self.tasks.store(store_id)
        members = list(dict.fromkeys(admins + members))
        self._people(store_id, members)
        cur = self.docs.get(st.TRACKERS, store_id) or {}
        doc = {**cur, "id": store_id, "store": store_id, "name": name.strip(), "admins": admins,
               "members": members, "tags": tags,
               "history": (cur.get("history") or []) + [
                   {"at": self.tasks.now().isoformat(), "by": by, "members": members, "admins": admins}]}
        self.docs.set(st.TRACKERS, store_id, doc)
        return doc

    def _people(self, store_id: str, ids: list[str]) -> None:
        known = {s["id"] for s in self.tasks.staff(store_id)}
        if any(i not in known for i in ids):
            raise svc.Invalid("pick people from the team list")

    def set_members(self, store_id: str, staff: dict, ids: list[str]) -> dict:
        b = self.board(store_id, staff)
        if staff["sid"] not in b["admins"]:
            raise auth.AuthError("only the person who runs this list can change who sees it", 403)
        members = list(dict.fromkeys(b["admins"] + [str(i) for i in ids]))
        self._people(store_id, members)
        b["members"] = members
        # Who could see the list, and when, is kept: each change is added, never replaced.
        b["history"] = (b.get("history") or []) + [
            {"at": self.tasks.now().isoformat(), "by": staff["sid"], "members": members, "admins": b["admins"]}]
        self.docs.set(st.TRACKERS, store_id, b)
        return self.view(store_id, staff)

    def view(self, store_id: str, staff: dict) -> dict:
        b = self.board(store_id, staff)
        items = sorted(self.docs.query(st.TRACKER_ITEMS, store=store_id), key=lambda i: (i["order"], i["created_at"]))
        return {"name": b["name"], "members": b["members"], "admin": staff["sid"] in b["admins"],
                "tags": b.get("tags") or [], "items": items}

    # ── items ───────────────────────────────────────────────────────────────

    def _clean(self, b: dict, data: dict, keys=EDITABLE) -> dict:
        out: dict = {}
        if "title" in keys and "title" in data:
            out["title"] = str(data["title"] or "").strip()[:MAX_TITLE]
            if not out["title"]:
                raise svc.Invalid("say what needs doing")
        if "detail" in keys and "detail" in data:
            out["detail"] = str(data["detail"] or "").strip()[:MAX_DETAIL]
        if "owners" in keys and "owners" in data:
            out["owners"] = list(dict.fromkeys(str(o) for o in data["owners"] or []))
            if any(o not in b["members"] for o in out["owners"]):
                raise svc.Invalid("an owner must be someone on this list")
        if "tags" in keys and "tags" in data:
            out["tags"] = [t for t in b.get("tags") or [] if t in (data["tags"] or [])]
        if "due" in keys and "due" in data:
            out["due"] = datetime.date.fromisoformat(data["due"]).isoformat() if data["due"] else None
        return out

    def parse(self, store_id: str, staff: dict, text: str) -> list[dict]:
        b = self.board(store_id, staff)
        people = [s for s in self.tasks.staff(store_id) if s["id"] in b["members"]]
        return parse(text or "", people, b.get("tags") or [], staff["sid"])

    def add(self, store_id: str, staff: dict, items: list[dict], at: str | None = None) -> list[dict]:
        b = self.board(store_id, staff)
        if not items or len(items) > MAX_ADDED:
            raise svc.Invalid(f"add between 1 and {MAX_ADDED} items at a time")
        stamp = at or self.tasks.now().isoformat()
        order = max((i["order"] for i in self.docs.query_fields(st.TRACKER_ITEMS, ["order"], store=store_id)), default=0)
        made = []
        for n, data in enumerate(items, 1):
            fields = {"detail": "", "owners": [], "tags": [], "due": None, **self._clean(b, data)}
            if "title" not in fields:
                raise svc.Invalid("say what needs doing")
            made.append({"id": f"a{uuid.uuid4().hex[:12]}", "store": store_id, **fields, "status": "open",
                         "order": order + n, "created_by": staff["sid"], "created_at": stamp,
                         "done_by": None, "done_at": None, "notes": [], "log": []})
        self.docs.set_many(st.TRACKER_ITEMS, made)
        return made

    def _item(self, store_id: str, staff: dict, item_id: str) -> tuple[dict, dict]:
        b = self.board(store_id, staff)
        item = self.docs.get(st.TRACKER_ITEMS, item_id)
        if item is None or item["store"] != store_id:
            raise svc.NotFound("item")
        return b, item

    def update(self, store_id: str, staff: dict, item_id: str, data: dict) -> dict:
        b, item = self._item(store_id, staff, item_id)
        stamp = self.tasks.now().isoformat()
        patch = self._clean(b, data)
        if data.get("status") in ("open", "done") and data["status"] != item["status"]:
            done = data["status"] == "done"
            patch.update(status=data["status"], done_by=staff["sid"] if done else None, done_at=stamp if done else None)
        changed = {k: v for k, v in patch.items() if item.get(k) != v}
        if not changed:
            return item
        # What an item said before is kept beside it.
        entry = {"at": stamp, "by": staff["sid"],
                 "was": {k: item.get(k) for k in changed if k not in ("done_by", "done_at")}}
        item = {**item, **changed, "updated_at": stamp, "updated_by": staff["sid"], "log": (item.get("log") or []) + [entry]}
        self.docs.set(st.TRACKER_ITEMS, item_id, item)
        return item

    def note(self, store_id: str, staff: dict, item_id: str, text: str, at: str | None = None) -> dict:
        _, item = self._item(store_id, staff, item_id)
        text = (text or "").strip()[:MAX_NOTE]
        if not text:
            raise svc.Invalid("write a note")
        item["notes"] = (item.get("notes") or []) + [
            {"id": f"n{uuid.uuid4().hex[:10]}", "by": staff["sid"], "at": at or self.tasks.now().isoformat(), "text": text}]
        self.docs.set(st.TRACKER_ITEMS, item_id, item)
        return item

    def remove(self, store_id: str, staff: dict, item_id: str) -> None:
        b, item = self._item(store_id, staff, item_id)
        if staff["sid"] not in (item["created_by"], *b["admins"]):
            raise auth.AuthError("only the person who added it or who runs this list can delete it", 403)
        self.docs.delete(st.TRACKER_ITEMS, item_id)
