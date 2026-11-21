"""Shift and closing-count summaries, posted to the store's ClickUp chat channel.

One post per shift (its due time + ``svc.ALERT_GRACE_MINUTES``) and one per closing count.
Each post is recorded in ``posts`` before it is sent, so a retry or a second instance
never posts twice; a send that fails is logged and left recorded (never re-sent blindly).
"""

from __future__ import annotations

import collections
import datetime
import logging
import re
import unicodedata
from zoneinfo import ZoneInfo

from cloud.store_tasks import model, service as svc, store as st
from cloud.store_tasks.clickup_import import named_weekday, template_id

log = logging.getLogger("store_tasks")

OPEN_LISTED = 8
SHOUTOUTS = 5
OTHER = "Other"
TITLE_CHARS = 60
MIRROR_WAIT = datetime.timedelta(hours=1)  # then post from what is stored rather than not at all


def channel(cfg: model.StoreConfig) -> str:
    return (cfg.clickup or {}).get("summary_channel", "")


def _day_label(day: datetime.date) -> str:
    return f"{day:%a %b} {day.day}"


def _names(tasks: svc.Tasks, store_id: str) -> dict[str, str]:
    return {s["id"]: " ".join(s["name"].split()) for s in tasks.staff(store_id, include_inactive=True)}


def _titles(rows: list[dict]) -> str:
    short = [t["title"] if len(t["title"]) <= TITLE_CHARS else t["title"][:TITLE_CHARS - 1].rstrip() + "…" for t in rows]
    more = len(short) - OPEN_LISTED
    return " · ".join(short[:OPEN_LISTED]) + (f" … and {more} more" if more > 0 else "")


def _missing_photo(tasks: svc.Tasks, store_id: str, done: list[dict], titles: dict[str, str]) -> list[dict]:
    """Done tasks whose definition needs a photo but that have none (ClickUp never enforced it)."""
    rules = {t.id for t in tasks.templates(store_id) if t.needs_photo}
    out = []
    for t in done:
        tid = t.get("template_id") or template_id(t["shift"], titles.get(t.get("parent_id") or ""), t["title"])
        if (t.get("needs_photo") or tid in rules) and not t.get("photos"):
            out.append(t)
    return out


def shift_summary(tasks: svc.Tasks, store_id: str, day: datetime.date, shift_id: str) -> str:
    cfg = tasks.store(store_id)
    every = tasks.ensure_day(cfg, day)
    titles = {t["id"]: t["title"] for t in every}
    mine = [t for t in every if t["shift"] == shift_id]
    top = [t for t in mine if not t.get("parent_id")]
    done = [t for t in top if t["status"] == model.DONE]
    left = sorted((t for t in top if t["status"] != model.DONE), key=model.sort_key)
    names = _names(tasks, store_id)
    lines = [f"**{cfg.shift(shift_id).label} duties · {_day_label(day)}**"]
    lines.append(f"{len(done)} of {len(top)} done" + (" 🎉" if top and not left else f" · {len(left)} still open"))
    people = collections.Counter(t["completed_by"] for t in done if t.get("completed_by"))
    if people:
        lines.append("Shoutouts: " + " · ".join(
            f"{names.get(sid, 'Shop' if sid == 'shop' else 'Someone')} {n}" for sid, n in people.most_common(SHOUTOUTS)))
    if left:
        lines.append("Still open: " + _titles(left))
    # ClickUp lets a task be ticked with its steps undone; ClickUp days also list every weekday's steps.
    steps = sorted((t for t in mine if t.get("parent_id") and t["status"] != model.DONE
                    and day.weekday() in (named_weekday(t["title"]) or (day.weekday(),))), key=model.sort_key)
    if steps:
        lines.append("Steps not ticked: " + _titles(steps))
    no_photo = _missing_photo(tasks, store_id, [t for t in mine if t["status"] == model.DONE], titles)
    if no_photo:
        lines.append("Done without the photo it needs: " + _titles(no_photo))
    return "\n".join(lines)


def _sections(cfg: model.StoreConfig) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for item in cfg.count_items:
        groups.setdefault(item.get("section") or OTHER, []).append(item)
    return list(groups.items())


def closing_update(tasks: svc.Tasks, store_id: str, day: datetime.date) -> str | None:
    cfg = tasks.store(store_id)
    c = tasks.count(store_id, day)
    if not c or not c.get("submitted_at"):
        return None
    tz = ZoneInfo(cfg.tz)
    at = datetime.datetime.fromisoformat(c["submitted_at"]).astimezone(tz)
    who = _names(tasks, store_id).get(c.get("submitted_by") or "", "")
    notes = c.get("notes") or {}
    lines = [f"**Daily closing update · {_day_label(day)}**",
             f"Sent{f' by {who}' if who else ''} at {at:%-I:%M %p}"]
    if notes.get("shift"):
        lines.append(f"Shift notes: {notes['shift']}")
    lines.append(f"Running low: {notes.get('low') or 'None'} · Out of: {notes.get('out') or 'None'}")
    if c.get("tips"):
        lines.append(f"Tips: {c['tips']}")
    items = c.get("items") or {}
    for section, rows in _sections(cfg):
        filled = [f"*   {i['name']}: {items[i['id']]}" for i in rows if items.get(i["id"])]
        if filled:
            lines += ["", f"**{section.upper()}**", *filled]
    if c.get("photos"):
        lines += ["", f"{len(c['photos'])} photo(s) in Store Tasks"]
    return "\n".join(lines)


def _due(cfg: model.StoreConfig, shift_id: str, day: datetime.date) -> datetime.datetime:
    return (datetime.datetime.fromisoformat(model.due_at(cfg, shift_id, day))
            + datetime.timedelta(minutes=svc.ALERT_GRACE_MINUTES))


def _mirrored_at(tasks: svc.Tasks, store_id: str) -> datetime.datetime | None:
    last = (tasks.docs.get(st.JOBS, store_id) or {}).get("mirror")
    return datetime.datetime.fromisoformat(last) if last else None


def pending(tasks: svc.Tasks, store_id: str) -> list[tuple[str, str]]:
    """``(post id, kind)`` due now and not yet posted. Reads no tasks."""
    cfg = tasks.store(store_id)
    if not channel(cfg):
        return []
    now = tasks.now()
    day = cfg.today(now)
    out = [(f"{store_id}_{day.isoformat()}_{s.id}", s.id) for s in cfg.shifts if now >= _due(cfg, s.id, day)]
    c = tasks.count(store_id, day)
    if c and c.get("submitted_at"):
        out.append((f"{store_id}_{day.isoformat()}_count_{c['submitted_at']}", "count"))
    return [(pid, kind) for pid, kind in out if tasks.docs.get(st.POSTS, pid) is None]


def wants_mirror(tasks: svc.Tasks, store_id: str) -> bool:
    """A ClickUp-owned day's shift summary waits (up to ``MIRROR_WAIT``) for a mirror run after the shift was due."""
    cfg = tasks.store(store_id)
    now = tasks.now()
    day = cfg.today(now)
    if cfg.generated(day):
        return False
    last = _mirrored_at(tasks, store_id)
    return any(kind != "count" and (last is None or last < _due(cfg, kind, day)) and now < _due(cfg, kind, day) + MIRROR_WAIT
               for _, kind in pending(tasks, store_id))


def post_due(tasks: svc.Tasks, store_id: str, send) -> list[str]:
    """Post every summary that is due. ``send(channel_id, markdown)`` posts one message."""
    cfg = tasks.store(store_id)
    day = cfg.today(tasks.now())
    sent = []
    for pid, kind in pending(tasks, store_id):
        if kind != "count" and wants_mirror(tasks, store_id):
            continue
        text = closing_update(tasks, store_id, day) if kind == "count" else shift_summary(tasks, store_id, day, kind)
        if not text or not tasks.docs.create(st.POSTS, pid, {"id": pid, "kind": kind, "at": tasks.now().isoformat()}):
            continue
        try:
            send(channel(cfg), text)
            sent.append(pid)
        except Exception as e:
            log.error("store-tasks fail reason=summary_post store=%s post=%s err=%r", store_id, pid, e)
    return sent


def _key(name: str) -> str:
    """ClickUp's posts and its field names disagree on accents (Açai / Açaí)."""
    return "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c)).casefold().strip()


_HEADING = re.compile(r"^\*\*(.+?)\*\*\s*$")
_ITEM = re.compile(r"^\*\s+(.+?):\s*(.*)$")


def sections_from_update(cfg: model.StoreConfig, count: dict, text: str) -> tuple[dict, ...]:
    """Closing-count fields in the ClickUp form's order, each with its section, read from one of
    ClickUp's "Daily Closing Update" posts. Two fields can share a name (Mango is a base and a
    fruit), so a line is matched by name and the value that day's count holds."""
    items = count.get("items") or {}
    unplaced = list(cfg.count_items)
    out: list[dict] = []
    section = None
    for line in text.splitlines():
        line = line.strip()
        h = _HEADING.match(line)
        if h:
            section = h.group(1).strip().title()
            continue
        m = _ITEM.match(line)
        if not (m and section):
            continue
        name, value = m.group(1).strip(), m.group(2).strip()
        same = [i for i in unplaced if _key(i["name"]) == _key(name)]
        pick = next((i for i in same if items.get(i["id"], "").strip() == value), same[0] if len(same) == 1 else None)
        if pick:
            unplaced.remove(pick)
            out.append({**pick, "section": section})
    return tuple(out + unplaced)
