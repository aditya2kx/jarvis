"""Store Tasks business logic. The only writer of the task collections."""

from __future__ import annotations

import dataclasses
import datetime
import io
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps

from cloud.store_tasks import auth, events as ev, model, store as st

MAX_HISTORY_DAYS = 62
MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 30 * 1024 * 1024  # under Cloud Run's 32 MiB request limit
VIDEO_TYPES = ("video/mp4", "video/quicktime", "video/webm")
ALERT_GRACE_MINUTES = 30
PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic")
RECENT_PHOTOS = 8  # per task definition: what the last few days' photos looked like
MAX_COMMENT = 2000
MAX_ASSIGN = 200
_READS = ThreadPoolExecutor(max_workers=16, thread_name_prefix="store-tasks-read")
PHOTO_SIZES = {"thumb": 480, "view": 1600}  # longest side, px
SIZED_DIR = "_sized"
_RESIZING = threading.BoundedSemaphore(2)  # decoding big photos is memory-hungry
# What the day's list reads from a definition: not its "earlier photos", the bulk of the document.
TEMPLATE_LIST_FIELDS = tuple(f.name for f in dataclasses.fields(model.Template)) + ("reference_photos",)
# Kept on a ClickUp task as a record; nothing on screen reads it.
NOT_LISTED = ("clickup",)
PLANNED = "planned"  # a ClickUp day's task the schedule expects but ClickUp has not added yet
FEEDBACK_KINDS = ("problem", "idea")
MAX_FEEDBACK_TEXT = 4000
MAX_FEEDBACK_EVENTS = 100
MAX_FEEDBACK_BYTES = 48 * 1024  # what was on screen and the last steps: plenty, and far under a Firestore doc
FEEDBACK_LISTED = 200


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def shrink(data: bytes, longest: int) -> bytes | None:
    """A JPEG no larger than ``longest`` px on its longest side; None when the photo already is."""
    try:
        img = Image.open(io.BytesIO(data))
        if max(img.size) <= longest and len(data) <= 400 * 1024:
            return None
        img.draft("RGB", (longest, longest))  # JPEG decodes at a fraction of full size: fast and small
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((longest, longest))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=80, optimize=True)
        return out.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _show_how_to(t: dict, definition: dict | None) -> None:
    """A ClickUp task shows its definition's how-to when there is one (ClickUp's text otherwise).
    Only what is shown: ClickUp's own text stays on the stored task."""
    if definition and definition.get("instructions"):
        t["instructions"] = definition["instructions"]


def planned(cfg: model.StoreConfig, day: datetime.date, tasks: list[dict],
            templates: list[model.Template]) -> list[dict]:
    """ClickUp adds a recurring task's next copy only once the current one is due, so days ahead look
    empty. Show what the schedule puts there instead, never stored, until ClickUp's own copy arrives."""
    def key(t: dict) -> tuple:
        return t["shift"], " ".join(t["title"].split()).lower()

    have = {k: t["id"] for t in tasks for k in (t.get("template_id"), key(t)) if k}
    built = model.build_day(cfg, templates, day)
    real = {t["id"]: have.get(t["template_id"]) or have.get(key(t)) for t in built}
    return [{**t, "source": PLANNED, "parent_id": real.get(t["parent_id"]) or t["parent_id"]}
            for t in built if not real[t["id"]]]


def public_staff(s: dict) -> dict:
    return {"id": s["id"], "name": s["name"], "role": s.get("role", auth.CREW),
            "active": s.get("active", True), "has_pin": bool(s.get("pin_hash")),
            "adp_name": s.get("adp_name") or "", "assignable": assignable(s)}


SHOP_STAFF = {"id": auth.SHOP, "name": "Shop", "role": auth.CREW, "active": True, "has_pin": False,
              "adp_name": "", "assignable": False}


CLICKUP_STATE = ("status", "assignee_id", "completed_by", "completed_at", "ticked_by", "assigned_by", "note")


def clickup_view(t: dict) -> dict:
    """A task as ClickUp has it, without any try-out made here (see ``Tasks._trial``)."""
    if not t.get("trial"):
        return t
    out = {k: v for k, v in t.items() if k not in ("trial", "clickup_state")}
    out.update(t.get("clickup_state") or {})
    out["photos"] = [p for p in t.get("photos") or [] if p.get("clickup_attachment_id")]
    out["comments"] = [c for c in t.get("comments") or [] if str(c.get("id", "")).startswith("cu")]
    return out


def assignable(s: dict) -> bool:
    """Only people ADP employs are picked to do a task. Before the first ADP sync, everyone active."""
    return s.get("active", True) and s.get("adp_active", True)


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
        """A person's own Google account is already proof of who they are: no PIN. A shop device is the shop."""
        if account.get("shop"):
            return {"session": auth.sign_session(auth.SHOP, account["store"], auth.CREW, account["email"],
                                                 self.now().timestamp()), "staff": SHOP_STAFF}
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
        if account.get("shop") and s.get("role") != auth.MANAGER:
            raise auth.AuthError("on the shop device only managers sign in", 403)
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

    # ── shop devices ────────────────────────────────────────────────────────

    def shop_code(self, store_id: str) -> dict:
        d = self.docs.get(st.SHOP_CODES, store_id) or {}
        return {"set": bool(d.get("code_hash")), "updated_at": d.get("updated_at")}

    def set_shop_code(self, store_id: str, code: str, by: str) -> None:
        """A new code also signs out every device that used the old one."""
        if not auth.valid_shop_code(code):
            raise Invalid("the shop code is 6 digits")
        for other in self.docs.query(st.SHOP_CODES):
            if other["id"] != store_id and auth.pin_matches(code, _as_pin(other)):
                raise Invalid("another store uses that code — pick a different one")
        h = auth.hash_pin(code)
        self.docs.set(st.SHOP_CODES, store_id, {
            "id": store_id, "code_hash": h["pin_hash"], "code_salt": h["pin_salt"], "gen": uuid.uuid4().hex,
            "updated_at": self.now().isoformat(), "updated_by": by})

    def enroll_shop(self, code: str) -> dict:
        """The code names the store. Wrong codes from anywhere count together, so guessing is slow."""
        now = self.now().timestamp()
        guard = self.docs.get(st.GUARDS, auth.SHOP) or {}
        if guard.get("locked_until", 0) > now:
            raise auth.AuthError("too many wrong codes — try again in a few minutes", 429)
        found = next((d for d in self.docs.query(st.SHOP_CODES)
                      if auth.valid_shop_code(code or "") and auth.pin_matches(code, _as_pin(d))), None)
        if found is None:
            fresh = now - guard.get("since", 0) > auth.CODE_LOCK_SECONDS  # misses count within one window
            misses = 1 if fresh else guard.get("misses", 0) + 1
            patch = {"id": auth.SHOP, "misses": misses, "since": now if fresh else guard["since"]}
            if misses >= auth.MAX_CODE_MISSES:
                patch = {"id": auth.SHOP, "misses": 0, "locked_until": now + auth.CODE_LOCK_SECONDS}
            self.docs.set(st.GUARDS, auth.SHOP, patch, merge=True)
            raise auth.AuthError("wrong code", 403)
        return {"device": auth.sign_device(found["id"], found["gen"], now)}

    def device_account(self, token: str) -> dict:
        """The account a device token stands for: the store's shop, or a signed-in Google account."""
        claims = auth.read_device(token, self.now().timestamp())
        if claims["kind"] == auth.GOOGLE:
            return self.account(claims["email"])
        d = self.docs.get(st.SHOP_CODES, claims["store"]) or {}
        if d.get("gen") != claims["gen"]:
            raise auth.AuthError("the shop code changed — enter the new one")
        return {"email": f"{auth.SHOP}:{claims['store']}", "store": claims["store"], "label": "Shop", "shop": True}

    def renew_device(self, account: dict, has_device: bool) -> str | None:
        if not has_device:
            return None
        if not account.get("shop"):
            return auth.sign_google(account["email"], self.now().timestamp())
        gen = (self.docs.get(st.SHOP_CODES, account["store"]) or {})["gen"]
        return auth.sign_device(account["store"], gen, self.now().timestamp())

    def google_device(self, email: str) -> str:
        """After the Google sign-in hop: a device token for the one public link."""
        return auth.sign_google(self.account(email)["email"], self.now().timestamp())

    # ── templates ───────────────────────────────────────────────────────────

    def templates(self, store_id: str) -> list[model.Template]:
        rows = [model.Template.from_dict(d) for d in self.docs.query(st.TEMPLATES, store=store_id)]
        return sorted(rows, key=lambda t: (t.shift, t.order, t.title))

    @staticmethod
    def _schedule(data: dict, today: datetime.date) -> dict:
        weekdays = tuple(sorted({int(w) for w in data.get("weekdays") or ()}))
        if any(w < 0 or w > 6 for w in weekdays):
            raise Invalid("weekdays are 0 (Monday) to 6 (Sunday)")
        month_days = tuple(sorted({int(d) for d in data.get("month_days") or ()}))
        if any(d != model.LAST_DAY and not 1 <= d <= 31 for d in month_days):
            raise Invalid("days of the month are 1 to 31")
        every = int(data.get("every_weeks") or 1)
        if not 1 <= every <= model.MAX_EVERY_WEEKS:
            raise Invalid(f"repeat every 1 to {model.MAX_EVERY_WEEKS} weeks")
        try:
            start, end = (datetime.date.fromisoformat(data[k]).isoformat() if data.get(k) else None
                          for k in ("start_date", "end_date"))
        except ValueError:
            raise Invalid("dates are YYYY-MM-DD") from None
        if start and end and end < start:
            raise Invalid("the last day is before the first day")
        if month_days:
            weekdays, every = (), 1
        elif every > 1 and not start:
            start = today.isoformat()  # the weeks are counted from here
        return {"weekdays": weekdays, "month_days": month_days, "every_weeks": every,
                "start_date": start, "end_date": end}

    def save_template(self, store_id: str, data: dict, by: str) -> model.Template:
        cfg = self.store(store_id)
        today = cfg.today(self.now())
        title = (data.get("title") or "").strip()
        if not title:
            raise Invalid("title is required")
        cfg.shift(data.get("shift") or "")  # KeyError → 400 in the route
        tid = data.get("id") or f"t{uuid.uuid4().hex[:10]}"
        cur = self.docs.get(st.TEMPLATES, tid)
        if cur and cur.get("store") != store_id:
            raise NotFound("template")
        parent_id = data.get("parent_id") or None
        if parent_id:
            parent = self.docs.get(st.TEMPLATES, parent_id)
            if parent is None or parent.get("store") != store_id or parent.get("parent_id") or parent_id == tid:
                raise Invalid("parent must be a top-level task in this store")
        t = model.Template(
            id=tid, store=store_id, shift=data["shift"], title=title,
            instructions=(data.get("instructions") or "").strip(), order=float(data.get("order") or 0),
            parent_id=parent_id, needs_photo=bool(data.get("needs_photo")),
            active=bool(data.get("active", True)), **self._schedule(data, today),
        )
        stamp = self.now().isoformat()
        if cur:
            # Superseded versions are kept, never overwritten.
            self.docs.set(st.TEMPLATE_HISTORY, f"{tid}_{stamp}", {
                **cur, "template_id": tid, "superseded_at": stamp, "superseded_by": by})
        kept = {k: cur[k] for k in ("reference_photos", "recent_photos") if cur and k in cur}
        if not cur and data.get("copy_from"):
            # A duplicate starts with the original's example photos.
            src = self.docs.get(st.TEMPLATES, data["copy_from"])
            if src and src.get("store") == store_id and src.get("reference_photos"):
                kept["reference_photos"] = src["reference_photos"]
        self.docs.set(st.TEMPLATES, tid, {**kept, **t.to_dict(), "updated_at": stamp, "updated_by": by})
        if cur:
            self._follow_edit(cfg, t, today, stamp)
        return t

    def reorder_templates(self, store_id: str, shift: str, ids: list[str], by: str) -> int:
        """A shift's top-level tasks in the order they are done; ids not given keep their place after.
        Earlier orders are kept in history, and lists from today on follow (ClickUp days show it too)."""
        cfg = self.store(store_id)
        cfg.shift(shift)
        cur = {t.id: t for t in self.templates(store_id) if t.shift == shift and not t.parent_id}
        if unknown := [i for i in ids if i not in cur]:
            raise Invalid(f"not a {shift} task: {unknown[0]}")
        rest = [t.id for t in sorted(cur.values(), key=lambda t: (t.order, t.title)) if t.id not in ids]
        today = cfg.today(self.now()).isoformat()
        stamp = self.now().isoformat()
        changed = 0
        for i, tid in enumerate(list(dict.fromkeys(ids)) + rest, 1):
            if cur[tid].order == i:
                continue
            old = self.docs.get(st.TEMPLATES, tid)
            self.docs.set(st.TEMPLATE_HISTORY, f"{tid}_{stamp}", {
                **old, "template_id": tid, "superseded_at": stamp, "superseded_by": by})
            self.docs.set(st.TEMPLATES, tid, {"order": float(i), "updated_at": stamp, "updated_by": by}, merge=True)
            for task in self.docs.query(st.TASKS, store=store_id, template_id=tid):
                if task["date"] >= today and task.get("source") != "clickup":
                    self.docs.set(st.TASKS, task["id"], {"order": float(i)}, merge=True)
            changed += 1
        return changed

    def _follow_edit(self, cfg: model.StoreConfig, t: model.Template, today: datetime.date, stamp: str) -> None:
        """Lists already built for today and later follow an edit; finished tasks keep what was done."""
        family = [t] + [c for c in self.templates(cfg.id) if c.parent_id == t.id]
        for tpl in family:
            for task in self.docs.query(st.TASKS, store=cfg.id, template_id=tpl.id):
                day = datetime.date.fromisoformat(task["date"])
                if day < today or task["status"] != model.OPEN or task.get("source") == "clickup":
                    continue  # ClickUp's copy is ClickUp's
                if not (model.due_on(tpl, day) and model.due_on(t, day)):
                    # No longer scheduled that day. Something already attached to it is kept.
                    if not (task.get("photos") or task.get("comments")):
                        self.docs.delete(st.TASKS, task["id"])
                elif tpl is t:
                    self.docs.set(st.TASKS, task["id"], {
                        "title": t.title, "instructions": t.instructions, "needs_photo": t.needs_photo,
                        "order": t.order, "shift": t.shift, "due_at": model.due_at(cfg, t.shift, day),
                        "updated_at": stamp}, merge=True)

    # ── days ────────────────────────────────────────────────────────────────

    def teams(self, cfg: model.StoreConfig, day: datetime.date, staff: list[dict],
              doc: dict | None = None) -> dict[str, list[dict]]:
        if doc is None:
            doc = self.docs.get(st.SHIFTS, f"{cfg.id}_{day.isoformat()}") or {}
        teams = {}
        for shift in cfg.shifts:
            members = []
            for name in model.shift_team(shift, doc.get("people") or []):
                s = model.match_staff(name, staff)
                members.append({"id": s["id"] if s else None, "name": s["name"] if s else name})
            teams[shift.id] = members
        return teams

    def _stored(self, cfg: model.StoreConfig, day: datetime.date, rows: list[dict] | None = None) -> list[dict]:
        """One owner per day: ClickUp's copy before cutover, the lists built here from then on."""
        if rows is None:
            rows = self.docs.query(st.TASKS, store=cfg.id, date=day.isoformat())
        mirrored = not cfg.generated(day)
        # A ClickUp step whose parent is on no list that day is not on ClickUp's list either.
        return [t for t in rows if (t.get("source") == "clickup") == mirrored and not t.get("stale_parent")]

    def ensure_day(self, cfg: model.StoreConfig, day: datetime.date, rows: list[dict] | None = None,
                   templates: list[model.Template] | None = None) -> list[dict]:
        """The day's tasks. Generated days are created on first read; ids make this an upsert."""
        tasks = self._stored(cfg, day, rows)
        if templates is None:
            templates = self.templates(cfg.id)
        if not cfg.generated(day):
            # ClickUp never enforced photos: show our rule on its tasks, so a missing photo is visible.
            rules = {t.id for t in templates if t.needs_photo}
            by_id = {t.id: t for t in templates}
            for t in tasks:
                tpl = by_id.get(t.get("template_id") or "")
                t["needs_photo"] = bool(t.get("needs_photo") or t.get("template_id") in rules)
                if tpl:  # shown in our order, with our how-to; ClickUp's copy stays as it is
                    _show_how_to(t, {"instructions": tpl.instructions})
                    t["order"] = tpl.order
        if cfg.generated(day):
            have = {t["id"] for t in tasks}
            missing = [t for t in model.build_day(cfg, templates, day) if t["id"] not in have]
            if missing and day <= cfg.today(self.now()) + datetime.timedelta(days=7):
                stamp = self.now().isoformat()
                created = [t for t in missing
                           if self.docs.create(st.TASKS, t["id"], {**t, "updated_at": stamp})]
                tasks += [{**t, "updated_at": stamp} for t in created]
        return sorted(tasks, key=lambda t: (t["shift"], *model.sort_key(t)))

    def day(self, store_id: str, day: datetime.date) -> dict:
        key = f"{store_id}_{day.isoformat()}"
        # Independent reads, fetched together: the list is the most-loaded screen (p99 target 500 ms).
        jobs = {
            "cfg": lambda: self.store(store_id),
            "staff": lambda: self.staff(store_id),
            "count": lambda: self.docs.get(st.COUNTS, key),
            "shifts": lambda: self.docs.get(st.SHIFTS, key) or {},
            "templates": lambda: self.docs.query_fields(st.TEMPLATES, TEMPLATE_LIST_FIELDS, store=store_id),
            "rows": lambda: self.docs.query(st.TASKS, store=store_id, date=day.isoformat()),
        }
        futures = {k: _READS.submit(f) for k, f in jobs.items()}
        got = {k: f.result() for k, f in futures.items()}
        cfg, staff, count = got["cfg"], got["staff"], got["count"]
        templates = sorted((model.Template.from_dict(d) for d in got["templates"]),
                           key=lambda t: (t.shift, t.order, t.title))
        tasks = self.ensure_day(cfg, day, got["rows"], templates)
        if not cfg.generated(day) and day > cfg.today(self.now()):
            tasks = sorted(tasks + planned(cfg, day, tasks, templates), key=lambda t: (t["shift"], *model.sort_key(t)))
        examples = {d["id"]: d["reference_photos"] for d in got["templates"] if d.get("reference_photos")}
        for t in tasks:  # the list shows which tasks have "how it should look" media
            for k in NOT_LISTED:
                t.pop(k, None)
            media = examples.get(t.get("template_id") or "") or t.get("reference_photos") or []
            t["examples"] = {"photos": sum(1 for p in media if not p.get("type")),
                             "videos": sum(1 for p in media if p.get("type"))}
        return {
            "store": store_id, "date": day.isoformat(), "today": cfg.today(self.now()).isoformat(),
            "generated": cfg.generated(day),
            "shifts": [{**s.to_dict(), "due_at": model.due_at(cfg, s.id, day)} for s in cfg.shifts],
            "teams": self.teams(cfg, day, staff, got["shifts"]),
            "tasks": tasks,
            "count_submitted": bool(count and count.get("submitted_at")),
        }

    def photo(self, path: str, size: str | None = None) -> tuple[bytes, str] | None:
        """A stored photo, or a smaller copy of it. ClickUp's originals are ~8 MB; a phone shows them
        as a thumbnail or at screen size, so those copies are made once and kept beside the original."""
        if size is None:
            return self.photos.get(path)
        if size not in PHOTO_SIZES:
            raise Invalid(f"size is one of {', '.join(PHOTO_SIZES)}")
        store_id, rest = path.split("/", 1)
        sized = f"{store_id}/{SIZED_DIR}/{size}/{rest}"
        found = self.photos.get(sized)
        if found is not None:
            return found
        found = self.photos.get(path)
        if found is None or found[1] not in PHOTO_TYPES:
            return found
        with _RESIZING:
            small = shrink(found[0], PHOTO_SIZES[size])
        if small is None:
            return found
        self.photos.put(sized, small, "image/jpeg")
        return small, "image/jpeg"

    def _task(self, task_id: str, store_id: str) -> dict:
        t = self.docs.get(st.TASKS, task_id)
        if t is None or t["store"] != store_id:
            raise NotFound("task")
        if t.get("source") == "clickup" and t.get("template_id"):
            _show_how_to(t, self.docs.get(st.TEMPLATES, t["template_id"]))
        return t

    def _trial(self, t: dict) -> dict:
        """ClickUp still owns this task: an edit here is a try-out. Keep ClickUp's state, once, so the
        sync can put it back (``reset_trials``); a ClickUp change to the task ends the try-out too.
        Days before today are history: they stay as ClickUp left them."""
        if t.get("source") != "clickup":
            return {}
        if t["date"] < self.store(t["store"]).today(self.now()).isoformat():
            raise Invalid("this day was done in ClickUp — it is read-only here")
        if t.get("clickup_state"):
            return {}
        return {"trial": True, "clickup_state": {k: t.get(k) for k in CLICKUP_STATE}}

    def _needs_photo(self, t: dict) -> bool:
        if t.get("needs_photo") or t.get("source") != "clickup" or not t.get("template_id"):
            return bool(t.get("needs_photo"))
        return bool((self.docs.get(st.TEMPLATES, t["template_id"]) or {}).get("needs_photo"))

    def _publish(self, t: dict, kind: str, store_id: str, data: dict) -> None:
        if t.get("source") != "clickup":  # a try-out is not news
            self.events.publish(kind, store_id, data)

    def complete(self, store_id: str, task_id: str, staff: dict, note: str = "",
                 at: str | None = None) -> dict:
        """Tick a task. A second tick (retry, another device) returns the first result unchanged."""
        t = self._task(task_id, store_id)
        if t["status"] == model.DONE:
            return t
        if self._needs_photo(t) and not t.get("photos"):
            raise Invalid("add a photo first")
        stamp = self.now().isoformat()
        # The shop device is not a person: the tick counts for whoever the task is assigned to.
        done_by = (t.get("assignee_id") or auth.SHOP) if staff["sid"] == auth.SHOP else staff["sid"]
        patch = {**self._trial(t), "status": model.DONE, "completed_by": done_by, "ticked_by": staff["sid"],
                 "completed_at": at or stamp, "note": (note or t.get("note") or "").strip()[:1000], "updated_at": stamp}
        self.docs.set(st.TASKS, task_id, patch, merge=True)
        t.update(patch)
        self._publish(t, ev.TASK_COMPLETED, store_id, {
            "task_id": task_id, "title": t["title"], "shift": t["shift"], "date": t["date"],
            "by": staff["sid"], "at": patch["completed_at"]})
        return t

    def reopen(self, store_id: str, task_id: str, staff: dict) -> dict:
        t = self._task(task_id, store_id)
        if t["status"] != model.DONE:
            return t
        if staff["role"] != auth.MANAGER and staff["sid"] not in (t.get("completed_by"), t.get("ticked_by")):
            raise auth.AuthError("only the person who ticked it or a manager can undo it", 403)
        patch = {**self._trial(t), "status": model.OPEN, "completed_by": None, "ticked_by": None,
                 "completed_at": None, "updated_at": self.now().isoformat()}
        self.docs.set(st.TASKS, task_id, patch, merge=True)
        t.update(patch)
        self._publish(t, ev.TASK_REOPENED, store_id, {"task_id": task_id, "by": staff["sid"]})
        return t

    def reset_trials(self, store_id: str, everything: bool = False) -> int:
        """Put ClickUp-owned tasks tried out here back as ClickUp has them: its tick and assignee, and
        only ClickUp's photos and notes. Days before today, or every day with ``everything``."""
        today = self.store(store_id).today(self.now()).isoformat()
        n = 0
        for t in self.docs.query(st.TASKS, store=store_id, trial=True):
            if t["date"] >= today and not everything:
                continue
            self.docs.set(st.TASKS, t["id"], clickup_view(t))
            n += 1
        return n

    @staticmethod
    def _photo(store_id: str, folder: str, data: bytes, content_type: str) -> str:
        if not data or len(data) > MAX_PHOTO_BYTES:
            raise Invalid("photo is empty or larger than 8 MB")
        if content_type not in PHOTO_TYPES:
            raise Invalid("photo must be a JPEG, PNG, WebP or HEIC image")
        return f"{store_id}/{folder}/{uuid.uuid4().hex}"

    def add_photo(self, store_id: str, task_id: str, staff: dict, data: bytes, content_type: str) -> dict:
        t = self._task(task_id, store_id)
        path = self._photo(store_id, f"{t['date']}/{task_id}", data, content_type)
        self.photos.put(path, data, content_type)
        stamp = self.now().isoformat()
        photo = {"path": path, "by": staff["sid"], "at": stamp}
        photos = (t.get("photos") or []) + [photo]
        patch = {**self._trial(t), "photos": photos, "updated_at": stamp}
        self.docs.set(st.TASKS, task_id, patch, merge=True)
        t.update(patch)
        tpl = self.docs.get(st.TEMPLATES, t["template_id"]) if t.get("template_id") else None
        if tpl and t.get("source") != "clickup":  # ClickUp's photos reach earlier-days via the sync
            recent = [{**photo, "date": t["date"]}] + (tpl.get("recent_photos") or [])
            self.docs.set(st.TEMPLATES, tpl["id"], {"recent_photos": recent[:RECENT_PHOTOS]}, merge=True)
        return t

    def remove_photo(self, store_id: str, task_id: str, path: str, staff: dict) -> dict:
        t = self._task(task_id, store_id)
        photo = next((p for p in t.get("photos") or [] if p["path"] == path), None)
        if photo is None:
            return t
        if t.get("source") == "clickup" and photo.get("clickup_attachment_id"):
            raise Invalid("this photo is in ClickUp — remove it there")
        if staff["role"] != auth.MANAGER and photo.get("by") != staff["sid"]:
            raise auth.AuthError("only the person who added it or a manager can remove it", 403)
        patch = {**self._trial(t), "photos": [p for p in t["photos"] if p["path"] != path],
                 "updated_at": self.now().isoformat()}
        self.docs.set(st.TASKS, task_id, patch, merge=True)
        t.update(patch)
        tpl = self.docs.get(st.TEMPLATES, t["template_id"]) if t.get("template_id") else None
        if tpl and any(p["path"] == path for p in tpl.get("recent_photos") or []):
            self.docs.set(st.TEMPLATES, tpl["id"], {
                "recent_photos": [p for p in tpl["recent_photos"] if p["path"] != path]}, merge=True)
        return t

    def _assignee(self, store_id: str, staff_id: str) -> None:
        s = self.docs.get(st.STAFF, staff_id)
        if s is None or s.get("store") != store_id:
            raise NotFound("person")
        if not assignable(s):
            raise Invalid("that person is not on the ADP roster")

    def assign(self, store_id: str, task_ids: list[str], assignee_id: str | None, staff: dict) -> list[dict]:
        """Anyone signed in can say who is doing a task, as in ClickUp. ``None`` clears it."""
        if not task_ids or len(task_ids) > MAX_ASSIGN:
            raise Invalid(f"pick between 1 and {MAX_ASSIGN} tasks")
        if assignee_id:
            self._assignee(store_id, assignee_id)
        picked = [self._task(i, store_id) for i in dict.fromkeys(task_ids)]
        patch = {"assignee_id": assignee_id or None, "assigned_by": staff["sid"],
                 "updated_at": self.now().isoformat()}
        for t in picked:
            if t.get("assignee_id") != patch["assignee_id"]:
                mine = {**self._trial(t), **patch}
                self.docs.set(st.TASKS, t["id"], mine, merge=True)
                t.update(mine)
        return picked

    def comment(self, store_id: str, task_id: str, staff: dict, text: str) -> dict:
        t = self._task(task_id, store_id)
        text = (text or "").strip()
        if not text:
            raise Invalid("write something first")
        stamp = self.now().isoformat()
        patch = {**self._trial(t), "updated_at": stamp, "comments": (t.get("comments") or []) + [
            {"id": uuid.uuid4().hex[:10], "by": staff["sid"], "at": stamp, "text": text[:MAX_COMMENT]}]}
        self.docs.set(st.TASKS, task_id, patch, merge=True)
        t.update(patch)
        return t

    def _definition(self, t: dict) -> tuple[str, dict]:
        """Where a task's how-to and example photos live: its recurring definition, else itself."""
        tpl = self.docs.get(st.TEMPLATES, t["template_id"]) if t.get("template_id") else None
        return (st.TEMPLATES, tpl) if tpl else (st.TASKS, t)

    def _planned_task(self, store_id: str, task_id: str) -> dict:
        prefix = f"{store_id}_"
        try:
            day = datetime.date.fromisoformat(task_id[len(prefix):len(prefix) + 10])
        except ValueError:
            raise NotFound("task") from None
        cfg = self.store(store_id)
        if not task_id.startswith(prefix) or cfg.generated(day) or day <= cfg.today(self.now()):
            raise NotFound("task")
        found = next((t for t in planned(cfg, day, self._stored(cfg, day), self.templates(store_id))
                      if t["id"] == task_id), None)
        if found is None:
            raise NotFound("task")
        return found

    def task_detail(self, store_id: str, task_id: str) -> dict:
        try:
            t = self._task(task_id, store_id)
        except NotFound:
            t = self._planned_task(store_id, task_id)
        coll, holder = self._definition(t)
        own = {p["path"] for p in t.get("photos") or []}
        recent = holder.get("recent_photos") or [] if coll == st.TEMPLATES else []
        return {"task": t, "reference_photos": holder.get("reference_photos") or [],
                "recent_photos": [p for p in recent if p["path"] not in own],
                "definition": model.Template.from_dict(holder).to_dict() if coll == st.TEMPLATES else None}

    def set_instructions(self, store_id: str, task_id: str, text: str, staff: dict) -> dict:
        """A manager's edit to the how-to. On a recurring task it changes every day from today."""
        t = self._task(task_id, store_id)
        coll, holder = self._definition(t)
        text = (text or "").strip()
        if coll == st.TEMPLATES:
            self.save_template(store_id, {**holder, "instructions": text}, staff["sid"])
        elif t.get("source") == "clickup":
            raise Invalid("this task's how-to is in ClickUp — change it there")
        if t.get("source") != "clickup":  # a ClickUp day keeps ClickUp's text; the definition carries the edit
            self.docs.set(st.TASKS, task_id, {"instructions": text, "updated_at": self.now().isoformat()}, merge=True)
        return self._task(task_id, store_id)

    def add_reference_photo(self, store_id: str, task_id: str, staff: dict, data: bytes,
                            content_type: str) -> dict:
        """An example photo, or a short video, of the task done right."""
        coll, holder = self._definition(self._task(task_id, store_id))
        video = content_type in VIDEO_TYPES
        if video and (not data or len(data) > MAX_VIDEO_BYTES):
            raise Invalid("video is empty or larger than 30 MB — keep it short")
        path = (f"{store_id}/ref/{holder['id']}/{uuid.uuid4().hex}" if video
                else self._photo(store_id, f"ref/{holder['id']}", data, content_type))
        self.photos.put(path, data, content_type)
        photos = (holder.get("reference_photos") or []) + [
            {"path": path, "by": staff["sid"], "at": self.now().isoformat(), **({"type": content_type} if video else {})}]
        self.docs.set(coll, holder["id"], {"reference_photos": photos}, merge=True)
        return self.task_detail(store_id, task_id)

    def remove_reference_photo(self, store_id: str, task_id: str, path: str) -> dict:
        coll, holder = self._definition(self._task(task_id, store_id))
        photos = [p for p in holder.get("reference_photos") or [] if p["path"] != path]
        self.docs.set(coll, holder["id"], {"reference_photos": photos}, merge=True)
        return self.task_detail(store_id, task_id)

    def add_one_off(self, store_id: str, data: dict, by: str) -> dict:
        if by == auth.SHOP:
            raise auth.AuthError("the shop account can't add tasks — a manager signs in first", 403)
        cfg = self.store(store_id)
        title = (data.get("title") or "").strip()
        if not title:
            raise Invalid("title is required")
        day = datetime.date.fromisoformat(data.get("date") or cfg.today(self.now()).isoformat())
        shift = cfg.shift(data.get("shift") or cfg.shifts[0].id)
        if data.get("assignee_id"):
            self._assignee(store_id, data["assignee_id"])
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
        if t.get("source") == "clickup":
            raise Invalid("this task is in ClickUp — delete it there")
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

    # ── feedback ────────────────────────────────────────────────────────────

    def add_feedback(self, store_id: str, staff: dict, account: dict, data: dict,
                     photo: tuple[bytes, str] | None = None) -> dict:
        """A problem or an idea from the crew, kept with what was on screen and their last steps."""
        text = (data.get("text") or "").strip()[:MAX_FEEDBACK_TEXT]
        if not text and photo is None:
            raise Invalid("say what happened, or add a photo")
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        events = [e for e in (data.get("events") or [])[-MAX_FEEDBACK_EVENTS:] if isinstance(e, dict)]
        while events and len(json.dumps([context, events])) > MAX_FEEDBACK_BYTES:
            events = events[len(events) // 4 + 1:]  # the latest steps matter most
        if len(json.dumps(context)) > MAX_FEEDBACK_BYTES:
            context = {"dropped": "too large"}
        fid = f"f{uuid.uuid4().hex[:12]}"
        names = {s["id"]: s["name"] for s in self.staff(store_id, include_inactive=True)}
        doc = {
            "id": fid, "store": store_id, "at": self.now().isoformat(), "status": "new",
            "kind": data.get("kind") if data.get("kind") in FEEDBACK_KINDS else FEEDBACK_KINDS[0],
            "text": text, "staff_id": staff["sid"], "staff_name": names.get(staff["sid"], "Shop"),
            "account": account.get("email"), "device": account.get("label") or "",
            "context": context, "events": events, "photos": [],
        }
        if photo is not None:
            path = self._photo(store_id, f"feedback/{fid}", *photo)
            self.photos.put(path, photo[0], photo[1])
            doc["photos"] = [{"path": path, "by": staff["sid"], "at": doc["at"]}]
        self.docs.set(st.FEEDBACK, fid, doc)
        return doc

    def feedback(self, store_id: str) -> list[dict]:
        rows = self.docs.query(st.FEEDBACK, store=store_id)
        return sorted(rows, key=lambda f: f["at"], reverse=True)[:FEEDBACK_LISTED]

    def close_feedback(self, store_id: str, fid: str, by: str, done: bool = True) -> dict:
        f = self.docs.get(st.FEEDBACK, fid)
        if f is None or f["store"] != store_id:
            raise NotFound("feedback")
        patch = {"status": "done" if done else "new", "done_by": by if done else None,
                 "done_at": self.now().isoformat() if done else None}
        self.docs.set(st.FEEDBACK, fid, patch, merge=True)
        return {**f, **patch}

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
        tasks = [clickup_view(t) for t in self._stored(cfg, day)]
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

    def job_done(self, store_id: str, job: str, started: datetime.datetime | None = None) -> None:
        self.docs.set(st.JOBS, store_id, {"id": store_id, job: (started or self.now()).isoformat()}, merge=True)


def _as_pin(code_doc: dict) -> dict:
    return {"pin_hash": code_doc.get("code_hash"), "pin_salt": code_doc.get("code_salt")}


def _later(a: str, b: str) -> bool:
    return datetime.datetime.fromisoformat(a) > datetime.datetime.fromisoformat(b)


def from_env() -> Tasks:
    if os.environ.get("STORE_TASKS_BACKEND", "firestore") == "memory":
        return Tasks(st.MemoryStore(), st.DirPhotoStore("/tmp/store-tasks-photos"), ev.Events(topic=""))
    return Tasks(st.FirestoreStore(), st.GcsPhotoStore(os.environ["STORE_TASKS_PHOTO_BUCKET"]), ev.Events())
