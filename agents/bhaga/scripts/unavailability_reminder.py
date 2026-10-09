#!/usr/bin/env python3
"""Unavailability reminder — ClickUp DM drafts before the Friday publish (Issue #381).

On the first configured day (default Wed) the operator gets a ready-to-post
"update your ADP unavailability" reminder for the week after next; on later
configured days (default Thu) a one-line follow-up for the same thread. Config
(on/off, days, time, both texts) is the ``automations`` row edited on the
Operator Console ``/automations/unavailability-reminder``. Sends at most one DM
per CT day; a tick before the configured time or on another day does nothing.

CLI:
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.unavailability_reminder --dry-run
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.unavailability_reminder --once
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.unavailability_reminder   # scheduler tick
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from typing import Any
from zoneinfo import ZoneInfo

from agents.bhaga.scripts import team_pulse as tp

logger = logging.getLogger(__name__)

AUTOMATION_ID = "unavailability-reminder"
DEFAULT_DAYS = [2, 3]  # Wed, Thu (Python weekday)
DEFAULT_HOUR = 10
PUBLISH_WEEKDAY = 4  # Friday

DEFAULT_TEMPLATE = (
    "@everyone Friendly reminder to update unavailability on ADP. Schedule for {target_week}, "
    "will be published this {publish_day} based entirely off your ADP unavailability. "
    "Please update it beforehand to avoid swapping shifts after that."
)
DEFAULT_FOLLOWUP = "One last reminder before shifts get published tomorrow"

_HEADERS = {
    "reminder": "Unavailability reminder for Shift Coverage & Trades — copy and post:",
    "followup": "Unavailability follow-up — post as a reply in this week's reminder thread:",
}


def _md(d: dt.date) -> str:
    return f"{d:%b} {d.day}"


def kind_for(today: dt.date, days: list[int]) -> str:
    """'reminder' on the earliest configured weekday, 'followup' on the others."""
    return "reminder" if not days or today.weekday() <= min(days) else "followup"


def compose(today: dt.date, days: list[int], template: str, followup: str) -> tuple[str, str]:
    """Return (kind, DM content) for ``today``. The schedule being collected is the week after next."""
    monday = today - dt.timedelta(days=today.weekday())
    target = monday + dt.timedelta(days=14)
    publish = monday + dt.timedelta(days=PUBLISH_WEEKDAY)
    kind = kind_for(today, days)
    text = template if kind == "reminder" else followup
    body = (
        text.replace("{target_week}", f"{_md(target)} - {_md(target + dt.timedelta(days=6))}")
        .replace("{publish_day}", f"{publish:%A}({_md(publish)})")
        .strip()
    )
    return kind, f"{_HEADERS[kind]}\n\n---\n\n{body}"


def is_due(now: dt.datetime, *, days: list[int], hour: int, minute: int, enabled: bool) -> bool:
    return enabled and now.weekday() in set(days) and (now.hour, now.minute) >= (hour, minute)


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
        "destination": "dm",
        "channel_id": None,
        "dm_user_id": tp.DEFAULT_DM_USER_ID,
        "workspace_id": tp.DEFAULT_WORKSPACE_ID,
        "template": DEFAULT_TEMPLATE,
        "followup_template": DEFAULT_FOLLOWUP,
        "updated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "updated_by": updated_by,
    }
    load_rows(
        "automations", [row], merge_keys=["store", "automation_id"],
        column_bq_types={"updated_at": "TIMESTAMP", "enabled": "BOOL"},
    )
    logger.info("unavailability_reminder: seeded default config store=%s", store)
    return row


def run_reminder(
    *,
    store: str = tp.DEFAULT_STORE,
    dry_run: bool = False,
    force: bool = False,
    trigger: str = "scheduler",
    updated_by: str = "unavailability_reminder",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """One tick. ``force`` (``--once``) skips the day/time/enabled gate, never the daily dedupe."""
    cfg = ensure_default_config(store, updated_by=updated_by)
    tz = str(cfg.get("timezone") or tp.DEFAULT_TZ)
    now = (now or dt.datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz))
    today = now.date()
    days = tp.parse_days(cfg.get("days_of_week")) if cfg.get("days_of_week") else list(DEFAULT_DAYS)
    hour = int(cfg.get("hour_local") if cfg.get("hour_local") is not None else DEFAULT_HOUR)
    minute = int(cfg.get("minute_local") or 0)
    enabled = bool(cfg.get("enabled"))

    if not force and not is_due(now, days=days, hour=hour, minute=minute, enabled=enabled):
        reason = f"skip: enabled={enabled} now={now:%a %H:%M} days={days} at={hour:02d}:{minute:02d}"
        return {"status": "skipped", "reason": reason, "post_date_ct": today.isoformat()}
    if not dry_run and tp.already_posted(store, AUTOMATION_ID, today):
        return {"status": "skipped", "reason": f"skip: already sent for {today}", "post_date_ct": today.isoformat()}

    kind, content = compose(
        today, days,
        str(cfg.get("template") or DEFAULT_TEMPLATE),
        str(cfg.get("followup_template") or DEFAULT_FOLLOWUP),
    )
    if dry_run:
        return {"status": "dry_run", "kind": kind, "content": content, "post_date_ct": today.isoformat()}

    from skills.clickup_chat import post_message

    dest, channel_id = tp.resolve_target_channel(cfg)
    created = post_message(channel_id, content, team_id=str(cfg.get("workspace_id") or tp.DEFAULT_WORKSPACE_ID))
    message_id = str(created.get("id") or "")
    tp.record_post(
        store=store, post_date_ct=today, destination=dest, channel_id=channel_id,
        message_id=message_id or None, content=content, dry_run=False, trigger=trigger,
        updated_by=updated_by, automation_id=AUTOMATION_ID,
    )
    logger.info("unavailability_reminder: sent kind=%s message_id=%s", kind, message_id)
    return {"status": "posted", "kind": kind, "message_id": message_id, "content": content,
            "post_date_ct": today.isoformat()}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--store", default=tp.DEFAULT_STORE)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--once", action="store_true", help="ignore the day/time gate (still once per CT day)")
    p.add_argument("--date", type=dt.date.fromisoformat, help="compose as if today were this date (with --dry-run)")
    a = p.parse_args(argv)
    now = None
    if a.date:
        if not a.dry_run:
            p.error("--date only works with --dry-run")
        now = dt.datetime.combine(a.date, dt.time(12), tzinfo=ZoneInfo(tp.DEFAULT_TZ))
    result = run_reminder(
        store=a.store, dry_run=a.dry_run, force=a.once or bool(a.date),
        trigger="once" if a.once else ("preview" if a.dry_run else "scheduler"),
        updated_by="cli", now=now,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "content"}, default=str))
    if result.get("content"):
        print("---")
        print(result["content"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
