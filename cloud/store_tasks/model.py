"""Store Tasks domain model: templates, daily lists, shift teams. Pure functions, no I/O."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

OPEN = "open"
DONE = "done"

_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*(AM|PM)?\s*$", re.IGNORECASE)


def minutes(hhmm: str) -> int:
    """'07:00', '7:00 AM' or '8:30 PM' → minutes after midnight."""
    m = _TIME.match(hhmm or "")
    if not m:
        raise ValueError(f"bad time {hhmm!r}")
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").upper()
    if ap == "PM" and h != 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    return h * 60 + mi


@dataclass(frozen=True)
class ShiftConfig:
    id: str
    label: str
    due: str  # HH:MM local; when the list should be finished
    team_from: str  # a person is on the team if a scheduled range overlaps [team_from, team_to]
    team_to: str

    @staticmethod
    def from_dict(d: dict) -> "ShiftConfig":
        return ShiftConfig(d["id"], d["label"], d["due"], d["team_from"], d["team_to"])

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "due": self.due,
                "team_from": self.team_from, "team_to": self.team_to}


@dataclass(frozen=True)
class StoreConfig:
    id: str
    name: str
    tz: str
    shifts: tuple[ShiftConfig, ...]
    cutover_date: str | None = None  # first date generated here instead of mirrored from ClickUp
    count_items: tuple[dict, ...] = ()  # closing-count fields: {"id", "name", "kind": "text"|"number"}
    clickup: dict = field(default_factory=dict)
    shifts_dataset: str = ""  # BigQuery dataset holding this store's ADP schedule (shared data)

    @staticmethod
    def from_dict(d: dict) -> "StoreConfig":
        return StoreConfig(
            id=d["id"], name=d["name"], tz=d.get("tz", "America/Chicago"),
            shifts=tuple(ShiftConfig.from_dict(s) for s in d["shifts"]),
            cutover_date=d.get("cutover_date"),
            count_items=tuple(d.get("count_items") or ()),
            clickup=d.get("clickup") or {}, shifts_dataset=d.get("shifts_dataset") or "",
        )

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "tz": self.tz,
                "shifts": [s.to_dict() for s in self.shifts],
                "cutover_date": self.cutover_date, "count_items": list(self.count_items),
                "clickup": self.clickup, "shifts_dataset": self.shifts_dataset}

    def shift(self, shift_id: str) -> ShiftConfig:
        for s in self.shifts:
            if s.id == shift_id:
                return s
        raise KeyError(shift_id)

    def today(self, now: datetime.datetime | None = None) -> datetime.date:
        return (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(ZoneInfo(self.tz)).date()

    def generated(self, day: datetime.date) -> bool:
        """True when this day's list is built from templates rather than mirrored from ClickUp."""
        return self.cutover_date is not None and day.isoformat() >= self.cutover_date


@dataclass(frozen=True)
class Template:
    id: str
    store: str
    shift: str
    title: str
    instructions: str = ""
    order: float = 0.0
    weekdays: tuple[int, ...] = ()  # () = every day; 0 = Monday
    parent_id: str | None = None
    needs_photo: bool = False
    active: bool = True

    @staticmethod
    def from_dict(d: dict) -> "Template":
        return Template(
            id=d["id"], store=d["store"], shift=d["shift"], title=d["title"],
            instructions=d.get("instructions") or "", order=float(d.get("order") or 0),
            weekdays=tuple(sorted(d.get("weekdays") or ())), parent_id=d.get("parent_id"),
            needs_photo=bool(d.get("needs_photo")), active=d.get("active", True),
        )

    def to_dict(self) -> dict:
        return {"id": self.id, "store": self.store, "shift": self.shift, "title": self.title,
                "instructions": self.instructions, "order": self.order,
                "weekdays": list(self.weekdays), "parent_id": self.parent_id,
                "needs_photo": self.needs_photo, "active": self.active}


def due_on(t: Template, day: datetime.date) -> bool:
    return t.active and (not t.weekdays or day.weekday() in t.weekdays)


def task_id(store: str, day: datetime.date, template_id: str) -> str:
    return f"{store}_{day.isoformat()}_{template_id}"


def due_at(store: StoreConfig, shift_id: str, day: datetime.date) -> str:
    m = minutes(store.shift(shift_id).due)
    local = datetime.datetime.combine(day, datetime.time(m // 60, m % 60), ZoneInfo(store.tz))
    return local.isoformat()


def build_day(store: StoreConfig, templates: list[Template], day: datetime.date) -> list[dict]:
    """The day's task documents. Ids are natural keys, so regenerating is an upsert."""
    by_id = {t.id: t for t in templates}
    out = []
    for t in templates:
        if not due_on(t, day):
            continue
        parent = by_id.get(t.parent_id) if t.parent_id else None
        if t.parent_id and (parent is None or not due_on(parent, day)):
            continue
        out.append({
            "id": task_id(store.id, day, t.id), "store": store.id, "date": day.isoformat(),
            "shift": t.shift, "title": t.title, "instructions": t.instructions, "order": t.order,
            "template_id": t.id,
            "parent_id": task_id(store.id, day, t.parent_id) if t.parent_id else None,
            "needs_photo": t.needs_photo, "due_at": due_at(store, t.shift, day),
            "status": OPEN, "assignee_id": None, "completed_by": None, "completed_at": None,
            "note": "", "photos": [], "source": "generated",
        })
    return sorted(out, key=sort_key)


def sort_key(task: dict) -> tuple:
    return (task.get("order") or 0, task.get("title") or "")


def parse_ranges(ranges: list[str]) -> list[tuple[int, int]]:
    """['6:30 AM - 3:30 PM'] → [(390, 930)]. Unparseable entries are skipped."""
    out = []
    for r in ranges or []:
        parts = re.split(r"\s+-\s+", r.strip())
        if len(parts) != 2:
            continue
        try:
            out.append((minutes(parts[0]), minutes(parts[1])))
        except ValueError:
            continue
    return out


def shift_team(shift: ShiftConfig, scheduled: list[dict]) -> list[str]:
    """Names of people whose scheduled range overlaps the shift's team window.

    ``scheduled`` rows are ``{"name": str, "ranges": [[start_min, end_min], ...]}``.
    """
    lo, hi = minutes(shift.team_from), minutes(shift.team_to)
    team = []
    for person in scheduled:
        if any(a < hi and b > lo for a, b in person.get("ranges") or []):
            team.append(person["name"])
    return team


def name_tokens(name: str) -> frozenset[str]:
    """'Johnson, Dolce J' and 'Dolce Johnson' share {'dolce', 'johnson'}; single letters drop."""
    return frozenset(t for t in re.split(r"[^a-z]+", (name or "").lower()) if len(t) > 1)


def match_staff(adp_name: str, staff: list[dict]) -> dict | None:
    """Staff record for an ADP schedule name: explicit ``adp_name`` first, then first+last tokens."""
    for s in staff:
        if s.get("adp_name") and name_tokens(s["adp_name"]) == name_tokens(adp_name):
            return s
    want = name_tokens(adp_name)
    best = None
    for s in staff:
        have = name_tokens(s.get("name") or "")
        if len(have) >= 2 and have <= want:
            if best is None or len(have) > len(name_tokens(best["name"])):
                best = s
    return best
