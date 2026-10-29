#!/usr/bin/env python3
"""Unavailability reminder — ClickUp drafts before the Friday publish (Issues #381, #435).

On the first configured day (default Wed) the operator gets a ready-to-post
"update your ADP unavailability" reminder; on later configured days (default
Thu) a one-line follow-up for the same thread that tags everyone with a week in
the publish horizon and no ADP unavailability marked. The schedule runs
``lead_weeks`` (default 4) ahead: each Friday publishes through the end of the
``lead_weeks``-th week after this one.

Delivery (``automations.destination``):
  * ``drafts`` — typed into the operator's private ClickUp channel through the
    web app (``skills/clickup_browser``) so ``@everyone`` and people are real
    chips that survive copy-paste. Needs a browser, so the webhook tick starts
    the Cloud Run job (``BHAGA_UNAVAIL_REMINDER``); a browser failure falls back
    to the REST DM.
  * ``dm`` — REST API DM to the operator.

Config (on/off, days, time, lead weeks, both texts) is the ``automations`` row
edited on the Operator Console ``/automations/unavailability-reminder``. Sends at
most one draft per CT day; a tick before the configured time or on another day
does nothing.

CLI:
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.unavailability_reminder --dry-run [--date D]
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.unavailability_reminder --once
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.unavailability_reminder   # scheduler tick
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import pathlib
import tempfile
from typing import Any, Callable
from zoneinfo import ZoneInfo

from agents.bhaga.scripts import team_pulse as tp

logger = logging.getLogger(__name__)

AUTOMATION_ID = "unavailability-reminder"
DEFAULT_DAYS = [2, 3]  # Wed, Thu (Python weekday)
DEFAULT_HOUR = 10
DEFAULT_LEAD_WEEKS = 4
MIN_LEAD_WEEKS, MAX_LEAD_WEEKS = 2, 8
PUBLISH_WEEKDAY = 4  # Friday
DRAFTS_CHANNEL_ID = "8cr6661-7217"  # private #jarvis-drafts, operator only
SESSION_PORTAL = "clickup"
LOCAL_SESSION_DIR = pathlib.Path(__file__).resolve().parents[3] / "local" / "sessions"

DEFAULT_TEMPLATE = (
    "@everyone Friendly reminder to update unavailability on ADP. Schedules through {through_date} "
    "will be published this {publish_day} based entirely off your ADP unavailability. "
    "Please keep it updated at least {lead_weeks} weeks in advance to avoid swapping shifts after that."
)
DEFAULT_FOLLOWUP = (
    "{flagged} One last reminder before shifts through {through_date} get published tomorrow. "
    "Please keep your ADP unavailability updated at least {lead_weeks} weeks ahead."
)

_HEADERS = {
    "reminder": "Unavailability reminder for Shift Coverage & Trades — copy and post:",
    "followup": "Unavailability follow-up — post as a reply in this week's reminder thread:",
}


def _md(d: dt.date) -> str:
    return f"{d:%b} {d.day}"


def _monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def kind_for(today: dt.date, days: list[int]) -> str:
    """'reminder' on the earliest configured weekday, 'followup' on the others."""
    return "reminder" if not days or today.weekday() <= min(days) else "followup"


def lead_weeks_of(cfg: dict) -> int:
    raw = cfg.get("lead_weeks")
    n = int(raw) if raw is not None else DEFAULT_LEAD_WEEKS
    return min(MAX_LEAD_WEEKS, max(MIN_LEAD_WEEKS, n))


def check_weeks(today: dt.date, lead_weeks: int) -> list[dt.date]:
    """Mondays of the weeks this Friday's publish can add: the week after next through the lead week."""
    monday = _monday(today)
    return [monday + dt.timedelta(days=7 * w) for w in range(2, lead_weeks + 1)]


def compose(
    today: dt.date,
    days: list[int],
    template: str,
    followup: str,
    *,
    lead_weeks: int = DEFAULT_LEAD_WEEKS,
    flagged: list[str] | tuple[str, ...] = (),
) -> tuple[str, str]:
    """Return (kind, draft content) for ``today``.

    ``{target_week}`` is the lead week (the one each Friday adds), ``{through_date}``
    its Sunday, ``{flagged}`` the mentions joined by spaces (dropped when empty).
    """
    monday = _monday(today)
    target = monday + dt.timedelta(days=7 * lead_weeks)
    publish = monday + dt.timedelta(days=PUBLISH_WEEKDAY)
    kind = kind_for(today, days)
    text = template if kind == "reminder" else followup
    tags = " ".join(flagged)
    if not tags:
        text = text.replace("{flagged} ", "").replace(" {flagged}", "")
    body = (
        text.replace("{flagged}", tags)
        .replace("{target_week}", f"{_md(target)} - {_md(target + dt.timedelta(days=6))}")
        .replace("{through_date}", _md(target + dt.timedelta(days=6)))
        .replace("{publish_day}", f"{publish:%A}({_md(publish)})")
        .replace("{lead_weeks}", str(lead_weeks))
        .strip()
    )
    return kind, f"{_HEADERS[kind]}\n\n---\n\n{body}"


def is_due(now: dt.datetime, *, days: list[int], hour: int, minute: int, enabled: bool) -> bool:
    return enabled and now.weekday() in set(days) and (now.hour, now.minute) >= (hour, minute)


def covers(row: dict, day: dt.date) -> bool:
    """Same rule as the console's ``coversDate``: a one-off date, or a weekly repeat through repeat_until."""
    first = row["first_date"]
    if day == first:
        return True
    until = row.get("repeat_until")
    weekday = row.get("repeat_weekday")
    return weekday is not None and day > first and (until is None or day <= until) and day.weekday() == int(weekday)


def unmarked(roster: list[str], rows: list[dict], weeks: list[dt.date]) -> list[str]:
    """People on ``roster`` with at least one of ``weeks`` that has no unavailability on any day."""
    by_person: dict[str, list[dict]] = {}
    for r in rows:
        by_person.setdefault(r["employee"], []).append(r)
    out = []
    for person in sorted(set(roster)):
        mine = by_person.get(person, [])
        for monday in weeks:
            if not any(covers(r, monday + dt.timedelta(days=i)) for r in mine for i in range(7)):
                out.append(person)
                break
    return out


def _name_parts(canonical: str) -> tuple[str, str]:
    """('first', 'last') from payroll 'Last, First M' (or 'First Last')."""
    raw = canonical.strip().lower()
    if "," in raw:
        last, first = (s.strip() for s in raw.split(",", 1))
        return (first.split() or [""])[0], last
    parts = raw.split()
    return (parts[0] if parts else ""), (parts[-1] if len(parts) > 1 else "")


def match_member(canonical: str, members: list[dict]) -> dict | None:
    """The one ClickUp member for a payroll name: full name, then first+last words, then a unique first name."""
    first, last = _name_parts(canonical)
    if not first:
        return None
    words = [(m, (m.get("username") or "").lower().split()) for m in members]
    for test in (
        lambda w: w == [first, last] if last else w == [first],
        lambda w: first in w and (not last or last in w),
        lambda w: bool(w) and w[0] == first,
    ):
        hits = [m for m, w in words if test(w)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    return None


def mention(canonical: str, members: list[dict]) -> str:
    """A ``[@Name](#user_mention#id)`` token, or plain ``@First Last`` when no single member matches."""
    m = match_member(canonical, members)
    if m:
        return f"[@{m['username']}](#user_mention#{m['id']})"
    return f"@{tp.display_name(canonical)}"


def load_roster(store: str) -> list[str]:
    """Active hourly staff from the latest ADP People Directory snapshot (console ``adpDirectoryRoster``)."""
    from core.datastore import fq, read_query

    s = tp._escape(store)
    rows = read_query(
        f"""
        WITH snap AS (
          SELECT employee_name, employment_status FROM {fq('adp_directory_status')}
          WHERE store = '{s}'
          QUALIFY scraped_at_utc = MAX(scraped_at_utc) OVER ()
        )
        SELECT COALESCE(al.canonical_name, snap.employee_name) AS employee
        FROM snap
        LEFT JOIN {fq('employee_aliases')} al ON al.store = '{s}' AND al.raw_name = snap.employee_name
        WHERE COALESCE(al.canonical_name, snap.employee_name) NOT IN (
          SELECT canonical_name FROM {fq('adp_wage_rates')}
          WHERE canonical_name IS NOT NULL
            AND (IFNULL(is_salaried, FALSE) OR IFNULL(excluded_from_labor_pct, FALSE))
        )
        GROUP BY employee
        HAVING LOGICAL_OR(snap.employment_status = 'Active')
        """
    )
    return [str(r["employee"]) for r in rows]


def load_unavailability(store: str, start: dt.date, end: dt.date) -> list[dict]:
    """Pending (unexpired) + approved ADP unavailability touching [start, end], names resolved."""
    from core.datastore import fq, read_query

    s = tp._escape(store)
    rows = read_query(
        f"""
        SELECT COALESCE(al.canonical_name, NULLIF(TRIM(u.raw_employee_name), ''), u.employee_name) AS employee,
               u.first_date, u.repeat_weekday, u.repeat_until
        FROM {fq('adp_unavailability')} u
        LEFT JOIN {fq('employee_aliases')} al ON al.store = '{s}' AND al.raw_name = TRIM(u.raw_employee_name)
        WHERE u.first_date <= DATE '{end.isoformat()}'
          AND (u.first_date >= DATE '{start.isoformat()}'
               OR (u.repeat_weekday IS NOT NULL
                   AND (u.repeat_until IS NULL OR u.repeat_until >= DATE '{start.isoformat()}')))
          AND NOT (u.status = 'pending' AND u.expires_at_ct <= CURRENT_DATETIME('America/Chicago'))
        """
    )
    return [
        {
            "employee": str(r["employee"]),
            "first_date": _as_date(r["first_date"]),
            "repeat_weekday": r.get("repeat_weekday"),
            "repeat_until": _as_date(r.get("repeat_until")),
        }
        for r in rows
    ]


def _as_date(v: Any) -> dt.date | None:
    if v is None or isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def flagged_mentions(store: str, today: dt.date, lead_weeks: int, workspace_id: str) -> list[str]:
    from skills.clickup_chat import list_members

    weeks = check_weeks(today, lead_weeks)
    if not weeks:
        return []
    rows = load_unavailability(store, weeks[0], weeks[-1] + dt.timedelta(days=6))
    names = unmarked(load_roster(store), rows, weeks)
    members = list_members(workspace_id)
    logger.info("unavailability_reminder: flagged %d of the roster for weeks %s",
                len(names), [w.isoformat() for w in weeks])
    return [mention(n, members) for n in names]


def ensure_default_config(store: str, *, updated_by: str) -> dict:
    from core.datastore import fq, load_rows, read_query

    rows = read_query(
        f"SELECT * FROM {fq('automations')}"
        f" WHERE store = '{tp._escape(store)}' AND automation_id = '{AUTOMATION_ID}' LIMIT 1"
    )
    if rows:
        return dict(rows[0])
    row = {
        "store": store,
        "automation_id": AUTOMATION_ID,
        "enabled": True,
        "days_of_week": json.dumps(DEFAULT_DAYS),
        "hour_local": DEFAULT_HOUR,
        "minute_local": 0,
        "timezone": tp.DEFAULT_TZ,
        "destination": "drafts",
        "channel_id": DRAFTS_CHANNEL_ID,
        "dm_user_id": tp.DEFAULT_DM_USER_ID,
        "workspace_id": tp.DEFAULT_WORKSPACE_ID,
        "template": DEFAULT_TEMPLATE,
        "followup_template": DEFAULT_FOLLOWUP,
        "lead_weeks": DEFAULT_LEAD_WEEKS,
        "updated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "updated_by": updated_by,
    }
    load_rows(
        "automations", [row], merge_keys=["store", "automation_id"],
        column_bq_types={"updated_at": "TIMESTAMP", "enabled": "BOOL", "lead_weeks": "INT64"},
    )
    logger.info("unavailability_reminder: seeded default config store=%s", store)
    return row


def browser_send(store: str, channel_id: str, content: str, workspace_id: str) -> dict:
    """Type ``content`` into ``channel_id`` through the ClickUp web app; persist the refreshed session."""
    from agents.bhaga.scripts import gcs_cache
    from skills._browser_runtime.runtime import on_cloud_run
    from skills.clickup_browser import send

    if on_cloud_run():
        path = pathlib.Path(tempfile.mkdtemp()) / f"{SESSION_PORTAL}-{store}.json"
        if not gcs_cache.download_session(path, portal=SESSION_PORTAL, store=store):
            raise RuntimeError(f"no ClickUp session at _session/{SESSION_PORTAL}-{store}.json")
    else:
        path = LOCAL_SESSION_DIR / f"{SESSION_PORTAL}-{store}.json"
        if not path.exists():
            raise RuntimeError(f"no ClickUp session at {path}")
    res = send(channel_id, content, team_id=workspace_id, storage_state=str(path))
    if on_cloud_run():
        gcs_cache.upload_session(path, portal=SESSION_PORTAL, store=store)
    return {"everyone": res.everyone, "users": res.users, "plain": res.plain}


def run_reminder(
    *,
    store: str = tp.DEFAULT_STORE,
    dry_run: bool = False,
    force: bool = False,
    trigger: str = "scheduler",
    updated_by: str = "unavailability_reminder",
    now: dt.datetime | None = None,
    in_browser_job: bool = False,
    start_browser_job: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """One tick. ``force`` (``--once``) skips the day/time/enabled gate, never the daily dedupe.

    A ``drafts`` config outside the browser job only starts the job (``start_browser_job``);
    the job re-runs this with ``in_browser_job=True`` and does the gate, dedupe and send.
    """
    cfg = ensure_default_config(store, updated_by=updated_by)
    tz = str(cfg.get("timezone") or tp.DEFAULT_TZ)
    now = (now or dt.datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz))
    today = now.date()
    days = tp.parse_days(cfg.get("days_of_week")) if cfg.get("days_of_week") else list(DEFAULT_DAYS)
    hour = int(cfg.get("hour_local") if cfg.get("hour_local") is not None else DEFAULT_HOUR)
    minute = int(cfg.get("minute_local") or 0)
    enabled = bool(cfg.get("enabled"))
    dest = str(cfg.get("destination") or "dm").strip().lower()
    workspace = str(cfg.get("workspace_id") or tp.DEFAULT_WORKSPACE_ID)
    base = {"post_date_ct": today.isoformat()}

    if not force and not is_due(now, days=days, hour=hour, minute=minute, enabled=enabled):
        reason = f"skip: enabled={enabled} now={now:%a %H:%M} days={days} at={hour:02d}:{minute:02d}"
        return {"status": "skipped", "reason": reason, **base}
    if not dry_run and tp.already_posted(store, AUTOMATION_ID, today):
        return {"status": "skipped", "reason": f"skip: already sent for {today}", **base}
    if dest == "drafts" and not dry_run and not in_browser_job:
        if start_browser_job is None:
            raise RuntimeError("drafts delivery needs the browser job; pass start_browser_job")
        started = start_browser_job()
        return {"status": "queued" if started else "skipped",
                "reason": "browser job started" if started else "skip: job busy, next tick retries", **base}

    lead = lead_weeks_of(cfg)
    kind = kind_for(today, days)
    template = str(cfg.get("template") or DEFAULT_TEMPLATE)
    followup = str(cfg.get("followup_template") or DEFAULT_FOLLOWUP)
    flagged: list[str] = []
    if "{flagged}" in (template if kind == "reminder" else followup):
        try:
            flagged = flagged_mentions(store, today, lead, workspace)
        except Exception as exc:  # noqa: BLE001 — the reminder still goes out untagged
            logger.error("BREADCRUMB unavailability_reminder flags failed store=%s date=%s: %s", store, today, exc)
    kind, content = compose(today, days, template, followup, lead_weeks=lead, flagged=flagged)
    if dry_run:
        return {"status": "dry_run", "kind": kind, "content": content, "flagged": flagged, **base}

    from skills.clickup_chat import post_message

    sent: dict[str, Any] = {}
    message_id = ""
    if dest == "drafts":
        channel_id = str(cfg.get("channel_id") or DRAFTS_CHANNEL_ID)
        try:
            sent = browser_send(store, channel_id, content, workspace)
        except Exception as exc:  # noqa: BLE001 — the operator still gets the draft, chips or not
            logger.error("BREADCRUMB unavailability_reminder browser send failed store=%s date=%s: %s — "
                         "falling back to the REST DM", store, today, exc)
            dest = "dm"
            content += f"\n\n(Sent by the API because the browser send failed: {str(exc)[:160]})"
    if dest != "drafts":
        dest, channel_id = tp.resolve_target_channel({**cfg, "destination": dest})
        created = post_message(channel_id, content, team_id=workspace)
        message_id = str(created.get("id") or "")
    tp.record_post(
        store=store, post_date_ct=today, destination=dest, channel_id=channel_id,
        message_id=message_id or None, content=content, dry_run=False, trigger=trigger,
        updated_by=updated_by, automation_id=AUTOMATION_ID,
    )
    logger.info("unavailability_reminder: sent kind=%s dest=%s message_id=%s chips=%s",
                kind, dest, message_id, sent)
    return {"status": "posted", "kind": kind, "destination": dest, "message_id": message_id,
            "content": content, "flagged": flagged, "chips": sent, **base}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--store", default=tp.DEFAULT_STORE)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--once", action="store_true", help="ignore the day/time gate (still once per CT day)")
    p.add_argument("--date", type=dt.date.fromisoformat, help="compose as if today were this date (with --dry-run)")
    p.add_argument("--in-browser-job", action="store_true",
                   default=bool(os.environ.get("BHAGA_UNAVAIL_REMINDER")),
                   help="send drafts through the browser here (the Cloud Run job, or a laptop with a session)")
    a = p.parse_args(argv)
    now = None
    if a.date:
        if not a.dry_run:
            p.error("--date only works with --dry-run")
        now = dt.datetime.combine(a.date, dt.time(12), tzinfo=ZoneInfo(tp.DEFAULT_TZ))
    result = run_reminder(
        store=a.store, dry_run=a.dry_run, force=a.once or bool(a.date),
        trigger="once" if a.once else ("preview" if a.dry_run else "scheduler"),
        updated_by="cli", now=now, in_browser_job=a.in_browser_job,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "content"}, default=str))
    if result.get("content"):
        print("---")
        print(result["content"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
