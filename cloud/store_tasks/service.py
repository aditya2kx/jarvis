"""Store Tasks business logic. The only writer of the task collections."""

from __future__ import annotations

import datetime
import os
import uuid
from zoneinfo import ZoneInfo

from cloud.store_tasks import auth, events as ev, model, store as st

MAX_HISTORY_DAYS = 62
MAX_PHOTO_BYTES = 8 * 1024 * 1024
ALERT_GRACE_MINUTES = 30
PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic")
RECENT_PHOTOS = 8  # per task definition: what the last few days' photos looked like
MAX_COMMENT = 2000
MAX_ASSIGN = 200


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def public_staff(s: dict) -> dict:
    return {"id": s["id"], "name": s["name"], "role": s.get("role", auth.CREW),
            "active": s.get("active", True), "has_pin": bool(s.get("pin_hash")),
            "adp_name": s.get("adp_name") or ""}


class Tasks:
    def __init__(self, docs: st.DocStore, photos: st.PhotoStore, events: ev.Events, now=_now) -> None:
        self.docs = docs
        self.photos = photos
        self.events = events
        self.now = now

    # ── store config ────────────────────────────────────────────────────────

    def store(self, store_id: str) -> model.StoreConfig:
        d = self.docs.get(st.STORES, store_id)
        if d is None:
            raise NotFound(f"store {store_id}")
        return model.StoreConfig.from_dict(d)

    def put_store(self, cfg: model.StoreConfig) -> None:
        self.docs.set(st.STORES, cfg.id, cfg.to_dict())

    # ── staff ───────────────────────────────────────────────────────────────

    def staff(self, store_id: str, include_inactive: bool = False) -> list[dict]:
        rows = self.docs.query(st.STAFF, store=store_id)
        if not include_inactive:
            rows = [r for r in rows if r.get("active", True)]
        return sorted(rows, key=lambda r: r["name"].lower())

    def save_staff(self, store_id: str, data: dict, by: str) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise Invalid("name is required")
        role = data.get("role") or auth.CREW
        if role not in (auth.CREW, auth.MANAGER):
            raise Invalid("role must be crew or manager")
        staff_id = data.get("id") or f"s{uuid.uuid4().hex[:10]}"
        cur = self.docs.get(st.STAFF, staff_id) or {}
        if cur and cur.get("store") != store_id:
            raise NotFound("staff")
        doc = {**cur, "id": staff_id, "store": store_id, "name": name, "role": role,
               "adp_name": (data.get("adp_name") or cur.get("adp_name") or "").strip(),
               "active": bool(data.get("active", True)),
               "updated_at": self.now().isoformat(), "updated_by": by}
        if cur.get("role") and cur["role"] != role:
            # PIN length depends on role, so a role change needs a fresh PIN.
            doc.pop("pin_hash", None)
            doc.pop("pin_salt", None)
        self.docs.set(st.STAFF, staff_id, doc)
        return doc

    def set_pin(self, store_id: str, staff_id: str, pin: str) -> None:
        s = self.docs.get(st.STAFF, staff_id)
        if s is None or s.get("store") != store_id:
            raise NotFound("staff")
        if not auth.valid_pin(pin, s.get("role", auth.CREW)):
            raise Invalid("PIN must be 4 digits (6 for managers)")
        self.docs.set(st.STAFF, staff_id, {**auth.hash_pin(pin), "pin_misses": 0, "locked_until": 0,
                                           "updated_at": self.now().isoformat()}, merge=True)

    # ── Google accounts and sessions ────────────────────────────────────────

    def account(self, email: str) -> dict:
        a = self.docs.get(st.ACCOUNTS, email)
        if a is None or not a.get("active", True):
            raise auth.AuthError("this Google account has no access to Store Tasks", 403)
        return a

    def accounts(self, store_id: str) -> list[dict]:
        return sorted(self.docs.query(st.ACCOUNTS, store=store_id), key=lambda a: a["email"])

    def save_account(self, store_id: str, data: dict, by: str) -> dict:
        """A shared account (store iPad: people pick their name and enter a PIN) or a person's own."""
        email = (data.get("email") or "").strip().lower()
        if "@" not in email or " " in email:
            raise Invalid("enter a Google account email")
        cur = self.docs.get(st.ACCOUNTS, email)
        if cur and cur.get("store") != store_id:
            raise Invalid("that account already belongs to another store")
        staff_id = data.get("staff_id") or None
        if staff_id:
            s = self.docs.get(st.STAFF, staff_id)
            if s is None or s.get("store") != store_id:
                raise NotFound("staff")
        doc = {"id": email, "email": email, "store": store_id, "staff_id": staff_id,
               "label": (data.get("label") or "").strip(), "active": True,
               "updated_at": self.now().isoformat(), "updated_by": by}
        self.docs.set(st.ACCOUNTS, email, doc)
        return doc

    def remove_account(self, store_id: str, email: str) -> None:
        a = self.docs.get(st.ACCOUNTS, email)
        if a is None or a["store"] != store_id:
            raise NotFound("account")
        self.docs.delete(st.ACCOUNTS, email)

    def own_session(self, account: dict) -> dict | None:
        """A person's own Google account is already proof of who they are: no PIN."""
        s = self.docs.get(st.STAFF, account.get("staff_id") or "") if account.get("staff_id") else None
        if s is None or not s.get("active", True) or s.get("store") != account["store"]:
            return None
        role = s.get("role", auth.CREW)
        return {"session": auth.sign_session(s["id"], s["store"], role, account["email"], self.now().timestamp()),
                "staff": public_staff(s)}

    def sign_in(self, account: dict, staff_id: str, pin: str) -> dict:
        s = self.docs.get(st.STAFF, staff_id)
        if s is None or s.get("store") != account["store"] or not s.get("active", True):
            raise auth.AuthError("unknown person", 403)
        now = self.now().timestamp()
        if s.get("locked_until", 0) > now:
            raise auth.AuthError("too many wrong PINs — try again in a few minutes", 429)
        if not auth.pin_matches(pin or "", s):
            misses = s.get("pin_misses", 0) + 1
            patch = {"pin_misses": misses}
            if misses >= auth.MAX_PIN_MISSES:
                patch = {"pin_misses": 0, "locked_until": now + auth.LOCK_SECONDS}
            self.docs.set(st.STAFF, staff_id, patch, merge=True)
            raise auth.AuthError("wrong PIN", 403)
        if s.get("pin_misses"):
            self.docs.set(st.STAFF, staff_id, {"pin_misses": 0}, merge=True)
        role = s.get("role", auth.CREW)
        return {"session": auth.sign_session(staff_id, s["store"], role, account["email"], now),
                "staff": public_staff(s)}

    # ── templates ───────────────────────────────────────────────────────────

    def templates(self, store_id: str) -> list[model.Template]:
        rows = [model.Template.from_dict(d) for d in self.docs.query(st.TEMPLATES, store=store_id)]
        return sorted(rows, key=lambda t: (t.shift, t.order, t.title))

    def save_template(self, store_id: str, data: dict, by: str) -> model.Template:
        cfg = self.store(store_id)
        title = (data.get("title") or "").strip()
        if not title:
            raise Invalid("title is required")
        cfg.shift(data.get("shift") or "")  # KeyError → 400 in the route
        weekdays = tuple(sorted({int(w) for w in data.get("weekdays") or ()}))
        if any(w < 0 or w > 6 for w in weekdays):
            raise Invalid("weekdays are 0 (Monday) to 6 (Sunday)")
        tid = data.get("id") or f"t{uuid.uuid4().hex[:10]}"
        cur = self.docs.get(st.TEMPLATES, tid)
        if cur and cur.get("store") != store_id:
            raise NotFound("template")
        parent_id = data.get("parent_id") or None
        if parent_id:
            parent = self.docs.get(st.TEMPLATES, parent_id)
            if parent is None or parent.get("store") != store_id or parent.get("parent_id"):
                raise Invalid("parent must be a top-level task in this store")
        t = model.Template(
            id=tid, store=store_id, shift=data["shift"], title=title,
            instructions=(data.get("instructions") or "").strip(), order=float(data.get("order") or 0),
            weekdays=weekdays, parent_id=parent_id, needs_photo=bool(data.get("needs_photo")),
            active=bool(data.get("active", True)),
        )
        stamp = self.now().isoformat()
        if cur:
            # Superseded versions are kept, never overwritten.
            self.docs.set(st.TEMPLATE_HISTORY, f"{tid}_{stamp}", {
                **cur, "template_id": tid, "superseded_at": stamp, "superseded_by": by})
        kept = {k: cur[k] for k in ("reference_photos", "recent_photos") if cur and k in cur}
        self.docs.set(st.TEMPLATES, tid, {**kept, **t.to_dict(), "updated_at": stamp, "updated_by": by})
        if cur:
            # Lists already built for today and later follow the edit; finished tasks keep what was done.
            today = cfg.today(self.now()).isoformat()
            patch = {"title": t.title, "instructions": t.instructions, "needs_photo": t.needs_photo,
                     "order": t.order, "updated_at": stamp}
            for task in self.docs.query(st.TASKS, store=store_id, template_id=tid):
                if task["date"] >= today and task["status"] == model.OPEN:
                    self.docs.set(st.TASKS, task["id"], patch, merge=True)
        return t

    # ── days ────────────────────────────────────────────────────────────────

    def teams(self, cfg: model.StoreConfig, day: datetime.date, staff: list[dict]) -> dict[str, list[dict]]:
        doc = self.docs.get(st.SHIFTS, f"{cfg.id}_{day.isoformat()}") or {}
        teams = {}
        for shift in cfg.shifts:
            members = []
            for name in model.shift_team(shift, doc.get("people") or []):
                s = model.match_staff(name, staff)
                members.append({"id": s["id"] if s else None, "name": s["name"] if s else name})
            teams[shift.id] = members
        return teams

    def _stored(self, cfg: model.StoreConfig, day: datetime.date) -> list[dict]:
        """One owner per day: ClickUp's copy before cutover, the lists built here from then on."""
        tasks = self.docs.query(st.TASKS, store=cfg.id, date=day.isoformat())
        mirrored = not cfg.generated(day)
        return [t for t in tasks if (t.get("source") == "clickup") == mirrored]

    def ensure_day(self, cfg: model.StoreConfig, day: datetime.date) -> list[dict]:
        """The day's tasks. Generated days are created on first read; ids make this an upsert."""
        tasks = self._stored(cfg, day)
        if cfg.generated(day):
            have = {t["id"] for t in tasks}
            missing = [t for t in model.build_day(cfg, self.templates(cfg.id), day) if t["id"] not in have]
            if missing and day <= cfg.today(self.now()) + datetime.timedelta(days=7):
                stamp = self.now().isoformat()
                created = [t for t in missing
                           if self.docs.create(st.TASKS, t["id"], {**t, "updated_at": stamp})]
                tasks += [{**t, "updated_at": stamp} for t in created]
        return sorted(tasks, key=lambda t: (t["shift"], *model.sort_key(t)))

    def day(self, store_id: str, day: datetime.date) -> dict:
        cfg = self.store(store_id)
        staff = self.staff(store_id)
        count = self.docs.get(st.COUNTS, f"{store_id}_{day.isoformat()}")
        return {
            "store": store_id, "date": day.isoformat(), "today": cfg.today(self.now()).isoformat(),
            "generated": cfg.generated(day),
            "shifts": [{**s.to_dict(), "due_at": model.due_at(cfg, s.id, day)} for s in cfg.shifts],
            "teams": self.teams(cfg, day, staff),
            "tasks": self.ensure_day(cfg, day),
            "count_submitted": bool(count and count.get("submitted_at")),
        }

    def _task(self, task_id: str, store_id: str) -> dict:
        t = self.docs.get(st.TASKS, task_id)
        if t is None or t["store"] != store_id:
            raise NotFound("task")
        return t

    @staticmethod
    def _writable(t: dict) -> None:
        if t.get("source") == "clickup":
            raise Invalid("this day was done in ClickUp — it is read-only here")

    def complete(self, store_id: str, task_id: str, staff: dict, note: str = "",
                 at: str | None = None) -> dict:
        """Tick a task. A second tick (retry, another device) returns the first result unchanged."""
        t = self._task(task_id, store_id)
        if t["status"] == model.DONE:
            return t
        self._writable(t)
        if t.get("needs_photo") and not t.get("photos") and staff["role"] != auth.MANAGER:
            raise Invalid("add a photo first")
        stamp = self.now().isoformat()
        patch = {"status": model.DONE, "completed_by": staff["sid"], "completed_at": at or stamp,
                 "note": (note or t.get("note") or "").strip()[:1000], "updated_at": stamp}
        self.docs.set(st.TASKS, task_id, patch, merge=True)
        t.update(patch)
        self.events.publish(ev.TASK_COMPLETED, store_id, {
            "task_id": task_id, "title": t["title"], "shift": t["shift"], "date": t["date"],
            "by": staff["sid"], "at": patch["completed_at"]})
        return t

    def reopen(self, store_id: str, task_id: str, staff: dict) -> dict:
        t = self._task(task_id, store_id)
        if t["status"] != model.DONE:
            return t
        self._writable(t)
        if staff["role"] != auth.MANAGER and t.get("completed_by") != staff["sid"]:
            raise auth.AuthError("only the person who ticked it or a manager can undo it", 403)
        patch = {"status": model.OPEN, "completed_by": None, "completed_at": None,
                 "updated_at": self.now().isoformat()}
        self.docs.set(st.TASKS, task_id, patch, merge=True)
        t.update(patch)
        self.events.publish(ev.TASK_REOPENED, store_id, {"task_id": task_id, "by": staff["sid"]})
        return t

    @staticmethod
    def _photo(store_id: str, folder: str, data: bytes, content_type: str) -> str:
        if not data or len(data) > MAX_PHOTO_BYTES:
            raise Invalid("photo is empty or larger than 8 MB")
        if content_type not in PHOTO_TYPES:
            raise Invalid("photo must be a JPEG, PNG, WebP or HEIC image")
        return f"{store_id}/{folder}/{uuid.uuid4().hex}"

    def add_photo(self, store_id: str, task_id: str, staff: dict, data: bytes, content_type: str) -> dict:
        t = self._task(task_id, store_id)
        self._writable(t)
        path = self._photo(store_id, f"{t['date']}/{task_id}", data, content_type)
        self.photos.put(path, data, content_type)
        stamp = self.now().isoformat()
        photo = {"path": path, "by": staff["sid"], "at": stamp}
        photos = (t.get("photos") or []) + [photo]
        self.docs.set(st.TASKS, task_id, {"photos": photos, "updated_at": stamp}, merge=True)
        t["photos"] = photos
        tpl = self.docs.get(st.TEMPLATES, t["template_id"]) if t.get("template_id") else None
        if tpl:
            recent = [{**photo, "date": t["date"]}] + (tpl.get("recent_photos") or [])
            self.docs.set(st.TEMPLATES, tpl["id"], {"recent_photos": recent[:RECENT_PHOTOS]}, merge=True)
        return t

    def remove_photo(self, store_id: str, task_id: str, path: str, staff: dict) -> dict:
        t = self._task(task_id, store_id)
        self._writable(t)
        photo = next((p for p in t.get("photos") or [] if p["path"] == path), None)
        if photo is None:
            return t
        if staff["role"] != auth.MANAGER and photo.get("by") != staff["sid"]:
            raise auth.AuthError("only the person who added it or a manager can remove it", 403)
        t["photos"] = [p for p in t["photos"] if p["path"] != path]
        self.docs.set(st.TASKS, task_id, {"photos": t["photos"], "updated_at": self.now().isoformat()}, merge=True)
        tpl = self.docs.get(st.TEMPLATES, t["template_id"]) if t.get("template_id") else None
        if tpl and any(p["path"] == path for p in tpl.get("recent_photos") or []):
            self.docs.set(st.TEMPLATES, tpl["id"], {
                "recent_photos": [p for p in tpl["recent_photos"] if p["path"] != path]}, merge=True)
        return t

    def assign(self, store_id: str, task_ids: list[str], assignee_id: str | None, staff: dict) -> list[dict]:
        """Anyone signed in can say who is doing a task, as in ClickUp. ``None`` clears it."""
        if not task_ids or len(task_ids) > MAX_ASSIGN:
            raise Invalid(f"pick between 1 and {MAX_ASSIGN} tasks")
        if assignee_id:
            s = self.docs.get(st.STAFF, assignee_id)
            if s is None or s.get("store") != store_id or not s.get("active", True):
                raise NotFound("person")
        picked = [self._task(i, store_id) for i in dict.fromkeys(task_ids)]
        for t in picked:
            self._writable(t)
        patch = {"assignee_id": assignee_id or None, "assigned_by": staff["sid"],
                 "updated_at": self.now().isoformat()}
        for t in picked:
            if t.get("assignee_id") != patch["assignee_id"]:
                self.docs.set(st.TASKS, t["id"], patch, merge=True)
                t.update(patch)
        return picked

    def comment(self, store_id: str, task_id: str, staff: dict, text: str) -> dict:
        t = self._task(task_id, store_id)
        self._writable(t)
        text = (text or "").strip()
        if not text:
            raise Invalid("write something first")
        stamp = self.now().isoformat()
        t["comments"] = (t.get("comments") or []) + [
            {"id": uuid.uuid4().hex[:10], "by": staff["sid"], "at": stamp, "text": text[:MAX_COMMENT]}]
        self.docs.set(st.TASKS, task_id, {"comments": t["comments"], "updated_at": stamp}, merge=True)
        return t

    def _definition(self, t: dict) -> tuple[str, dict]:
        """Where a task's how-to and example photos live: its recurring definition, else itself."""
        tpl = self.docs.get(st.TEMPLATES, t["template_id"]) if t.get("template_id") else None
        return (st.TEMPLATES, tpl) if tpl else (st.TASKS, t)

    def task_detail(self, store_id: str, task_id: str) -> dict:
        t = self._task(task_id, store_id)
        coll, holder = self._definition(t)
        own = {p["path"] for p in t.get("photos") or []}
        recent = holder.get("recent_photos") or [] if coll == st.TEMPLATES else []
        return {"task": t, "reference_photos": holder.get("reference_photos") or [],
                "recent_photos": [p for p in recent if p["path"] not in own]}

    def set_instructions(self, store_id: str, task_id: str, text: str, staff: dict) -> dict:
        """A manager's edit to the how-to. On a recurring task it changes every day from today."""
        t = self._task(task_id, store_id)
        self._writable(t)
        coll, holder = self._definition(t)
        text = (text or "").strip()
        if coll == st.TEMPLATES:
            self.save_template(store_id, {**holder, "instructions": text}, staff["sid"])
        self.docs.set(st.TASKS, task_id, {"instructions": text, "updated_at": self.now().isoformat()}, merge=True)
        return self._task(task_id, store_id)

    def add_reference_photo(self, store_id: str, task_id: str, staff: dict, data: bytes,
                            content_type: str) -> dict:
        t = self._task(task_id, store_id)
        self._writable(t)
        coll, holder = self._definition(t)
        path = self._photo(store_id, f"ref/{holder['id']}", data, content_type)
        self.photos.put(path, data, content_type)
        photos = (holder.get("reference_photos") or []) + [
            {"path": path, "by": staff["sid"], "at": self.now().isoformat()}]
        self.docs.set(coll, holder["id"], {"reference_photos": photos}, merge=True)
        return self.task_detail(store_id, task_id)

    def remove_reference_photo(self, store_id: str, task_id: str, path: str) -> dict:
        coll, holder = self._definition(self._task(task_id, store_id))
        photos = [p for p in holder.get("reference_photos") or [] if p["path"] != path]
        self.docs.set(coll, holder["id"], {"reference_photos": photos}, merge=True)
        return self.task_detail(store_id, task_id)

    def add_one_off(self, store_id: str, data: dict, by: str) -> dict:
        cfg = self.store(store_id)
        title = (data.get("title") or "").strip()
        if not title:
            raise Invalid("title is required")
        day = datetime.date.fromisoformat(data.get("date") or cfg.today(self.now()).isoformat())
        shift = cfg.shift(data.get("shift") or cfg.shifts[0].id)
        tid = data.get("id") or f"{store_id}_{day.isoformat()}_o{uuid.uuid4().hex[:10]}"
        t = {"id": tid, "store": store_id, "date": day.isoformat(), "shift": shift.id, "title": title,
             "instructions": (data.get("instructions") or "").strip(), "order": 1000.0,
             "template_id": None, "parent_id": None, "needs_photo": bool(data.get("needs_photo")),
             "due_at": model.due_at(cfg, shift.id, day), "status": model.OPEN,
             "assignee_id": data.get("assignee_id") or None, "completed_by": None, "completed_at": None,
             "note": "", "photos": [], "comments": [], "source": data.get("source") or "oneoff",
             "created_by": by,
             "updated_at": self.now().isoformat()}
        if self.docs.create(st.TASKS, tid, t):
            self.events.publish(ev.TASK_CREATED, store_id, {
                "task_id": tid, "title": title, "shift": shift.id, "date": t["date"],
                "assignee_id": t["assignee_id"], "by": by, "source": t["source"]})
            return t
        return self._task(tid, store_id)

    def remove_one_off(self, store_id: str, task_id: str, staff: dict) -> None:
        """Delete a task someone added for one day. Recurring tasks are changed in their definition."""
        t = self.docs.get(st.TASKS, task_id)
        if t is None or t["store"] != store_id:
            return
        self._writable(t)
        if t.get("template_id"):
            raise Invalid("this task comes back every day — a manager changes it under Manage → Tasks")
        if staff["role"] != auth.MANAGER and t.get("created_by") != staff["sid"]:
            raise auth.AuthError("only the person who added it or a manager can delete it", 403)
        self.docs.delete(st.TASKS, task_id)

    # ── closing count ───────────────────────────────────────────────────────

    def submit_count(self, store_id: str, day: datetime.date, data: dict, staff: dict) -> dict:
        cfg = self.store(store_id)
        known = {i["id"] for i in cfg.count_items}
        items = {k: str(v).strip() for k, v in (data.get("items") or {}).items()
                 if k in known and str(v).strip()}
        if not items:
            raise Invalid("enter at least one count")
        stamp = self.now()
        doc_id = f"{store_id}_{day.isoformat()}"
        prev = self.docs.get(st.COUNTS, doc_id)
        photos = (prev or {}).get("photos") or []
        if prev and not prev.get("submitted_at"):
            prev = None  # only photos so far: nothing was sent yet, so there is no earlier version
        doc = {"id": doc_id, "store": store_id, "date": day.isoformat(), "items": items,
               "notes": {k: (data.get("notes") or {}).get(k, "").strip()
                         for k in ("shift", "low", "out")},
               "tips": data.get("tips"), "submitted_by": staff["sid"],
               "submitted_at": stamp.isoformat(), "source": "app", "photos": photos,
               "revisions": (prev or {}).get("revisions", []) + ([{
                   k: prev[k] for k in ("items", "notes", "tips", "submitted_by", "submitted_at")
               }] if prev else [])}
        self.docs.set(st.COUNTS, doc_id, doc)
        self.events.publish(ev.COUNT_SUBMITTED, store_id, {
            "date": doc["date"], "by": staff["sid"], "items": len(items), "notes": doc["notes"],
            "tips": doc["tips"]})
        return doc

    def add_count_photo(self, store_id: str, day: datetime.date, staff: dict, data: bytes,
                        content_type: str) -> dict:
        """Photos sent with the closing count (deep clean, freezer). Can be added before the count."""
        self.store(store_id)
        doc_id = f"{store_id}_{day.isoformat()}"
        cur = self.docs.get(st.COUNTS, doc_id) or {
            "id": doc_id, "store": store_id, "date": day.isoformat(), "items": {},
            "notes": {"shift": "", "low": "", "out": ""}, "tips": None, "source": "draft"}
        path = self._photo(store_id, f"{day.isoformat()}/count", data, content_type)
        self.photos.put(path, data, content_type)
        cur["photos"] = (cur.get("photos") or []) + [
            {"path": path, "by": staff["sid"], "at": self.now().isoformat()}]
        self.docs.set(st.COUNTS, doc_id, cur)
        return cur

    def count(self, store_id: str, day: datetime.date) -> dict | None:
        return self.docs.get(st.COUNTS, f"{store_id}_{day.isoformat()}")

    def counts_as_clickup(self, store_id: str, since: str | None = None) -> list[dict]:
        """Submissions in the ClickUp closing-form task shape ``ingest_inventory`` already reads."""
        cfg = self.store(store_id)
        tz = ZoneInfo(cfg.tz)
        names = {i["id"]: i["name"] for i in cfg.count_items}
        out = []
        for c in self.docs.query(st.COUNTS, store=store_id):
            if c.get("source") != "app" or (since and c["submitted_at"] < since):
                continue
            local = datetime.datetime.fromisoformat(c["submitted_at"]).astimezone(tz)
            out.append({
                "id": f"st-{c['id']}",
                "name": f"Form Submission - #{local.replace(microsecond=0).isoformat()}",
                "date_created": str(int(local.timestamp() * 1000)),
                "custom_fields": [{"id": k, "name": names[k], "value": v}
                                  for k, v in c["items"].items() if k in names],
            })
        return sorted(out, key=lambda t: t["date_created"])

    # ── history and alerts ──────────────────────────────────────────────────

    def history(self, store_id: str, start: datetime.date, end: datetime.date) -> list[dict]:
        cfg = self.store(store_id)
        if end < start or (end - start).days > MAX_HISTORY_DAYS:
            raise Invalid(f"pick a range of at most {MAX_HISTORY_DAYS} days")
        out = []
        day = start
        while day <= end:
            tasks = self._stored(cfg, day)
            shifts = {}
            for s in cfg.shifts:
                mine = [t for t in tasks if t["shift"] == s.id]
                done = [t for t in mine if t["status"] == model.DONE]
                shifts[s.id] = {
                    "total": len(mine), "done": len(done),
                    "late": sum(1 for t in done if t.get("completed_at") and t.get("due_at")
                                and _later(t["completed_at"], t["due_at"])),
                }
            people: dict[str, int] = {}
            for t in tasks:
                if t["status"] == model.DONE and t.get("completed_by"):
                    people[t["completed_by"]] = people.get(t["completed_by"], 0) + 1
            out.append({"date": day.isoformat(), "shifts": shifts, "people": people})
            day += datetime.timedelta(days=1)
        return out

    def check_shifts(self, store_id: str) -> list[dict]:
        """Publish one ``shift.incomplete`` per shift that is past due + grace with open tasks."""
        cfg = self.store(store_id)
        now = self.now()
        day = cfg.today(now)
        tasks = self._stored(cfg, day)
        sent = []
        for s in cfg.shifts:
            due = datetime.datetime.fromisoformat(model.due_at(cfg, s.id, day))
            if now < due + datetime.timedelta(minutes=ALERT_GRACE_MINUTES):
                continue
            open_tasks = [t for t in tasks if t["shift"] == s.id and t["status"] != model.DONE]
            if not open_tasks:
                continue
            alert_id = f"{store_id}_{day.isoformat()}_{s.id}"
            if not self.docs.create(st.ALERTS, alert_id, {"id": alert_id, "at": now.isoformat()}):
                continue
            total = sum(1 for t in tasks if t["shift"] == s.id)
            payload = {"date": day.isoformat(), "shift": s.id, "label": s.label, "open": len(open_tasks),
                       "total": total,
                       "titles": [t["title"] for t in sorted(open_tasks, key=model.sort_key)][:10]}
            self.events.publish(ev.SHIFT_INCOMPLETE, store_id, payload)
            sent.append(payload)
        return sent

    # ── scheduled jobs ──────────────────────────────────────────────────────

    def job_due(self, store_id: str, job: str, every: datetime.timedelta) -> bool:
        last = (self.docs.get(st.JOBS, store_id) or {}).get(job)
        return last is None or self.now() - datetime.datetime.fromisoformat(last) >= every

    def job_done(self, store_id: str, job: str) -> None:
        self.docs.set(st.JOBS, store_id, {"id": store_id, job: self.now().isoformat()}, merge=True)


def _later(a: str, b: str) -> bool:
    return datetime.datetime.fromisoformat(a) > datetime.datetime.fromisoformat(b)


def from_env() -> Tasks:
    if os.environ.get("STORE_TASKS_BACKEND", "firestore") == "memory":
        return Tasks(st.MemoryStore(), st.DirPhotoStore("/tmp/store-tasks-photos"), ev.Events(topic=""))
    return Tasks(st.FirestoreStore(), st.GcsPhotoStore(os.environ["STORE_TASKS_PHOTO_BUCKET"]), ev.Events())
