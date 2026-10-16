#!/usr/bin/env python3
"""Daily missing-punch check in ClickUp Shift Coverage & Trades (Issue #397).

At 10:00 CT, if anything is open, one post lists every open missing punch from
the last two weeks with an inline @mention of the person:

  * Missing clock-in or clock-out — "we'll use your scheduled time".
  * Scheduled with no punch at all — "not paid until you confirm".

Staff reply in the post's thread; BHAGA never replies. The reply sweep (21:00
CT daily, and Monday 06:00 CT before the 07:00 payroll draft) reads each new
reply, matches its author to the person by email (ADP profile email ==
ClickUp email), and parses it (``skills/bhaga_labor/punch_reply.py``). An
understood reply becomes a ``punch_gap_decisions`` row (``decided_by
clickup:<Name>``); with ``BHAGA_PUNCH_CHECK_APPLY=1`` the webhook claims those
rows and starts the same apply-only job as the console's "Write to ADP". Once
every decision from a reply is written (or dismissed) the reply gets a ✅. An
unclear reply, or a write that failed, leaves the item open — it is listed in
the next day's post. At the Monday sweep, open missing clock-ins/outs with a
schedule are accepted at the scheduled time; a day with no punch never is.

Config (on/off, time, channel, closing line = ``template``) is the
``automations`` row ``punch-check``. Runs on the 15-minute
``bhaga-unavailability-reminder`` tick (``/unavailability-reminder``).

CLI:
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.punch_check --dry-run
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.punch_check --to-dm   # demo to the operator
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.punch_check --sweep-dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import uuid
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from agents.bhaga.scripts import team_pulse as tp
from skills.bhaga_labor.punch_gaps import (
    MISSING_IN, MISSING_OUT, MISSING_OUT_AFTER_BREAK, NO_ENTRY, merge_ranges,
)

logger = logging.getLogger(__name__)

AUTOMATION_ID = "punch-check"
DEFAULT_CHANNEL_ID = "8cr6661-1617"  # Shift Coverage & Trades
DEFAULT_HOUR = 10
DEFAULT_FOOTER = "🙏 Clock in & out every shift."
APPLY_FLAG = "BHAGA_PUNCH_CHECK_APPLY"
LOOKBACK_DAYS = 14
THREAD_LOOKBACK_DAYS = 8
EVENING_SWEEP = (21, 0)
PAYROLL_SWEEP = (6, 0)  # Monday, before the 07:00 bhaga-payroll-draft
PAYROLL_SWEEP_UNTIL = (7, 0)
STAFF_PREFIX = "clickup:"
PAYROLL_DEFAULT_BY = "auto:payroll-default"

FILLS = (MISSING_OUT, MISSING_OUT_AFTER_BREAK, MISSING_IN)
# A gap whose live decision is one of these is settled (or being written).
SETTLED = ("applied", "applying", "pending_write", "already_resolved", "not_in_hours", "rejected")
DONE = ("applied", "already_resolved", "not_in_hours", "rejected")


# ── Compose (pure) ───────────────────────────────────────────────


def clock(hhmm: Optional[str]) -> str:
    """'09:02' → '9:02a', '15:00' → '3:00p'."""
    h, m = (int(x) for x in (hhmm or "00:00").split(":"))
    return f"{(h % 12) or 12}:{m:02d}{'p' if h >= 12 else 'a'}"


def _day(d: dt.date) -> str:
    return f"{d:%a} {d.month}/{d.day}"


def _sched(ranges: list) -> str:
    spans = merge_ranges([tuple(r) for r in ranges or []])
    if not spans:
        return "No shift scheduled"
    return "Sched " + ", ".join(
        f"{clock(f'{a // 60:02d}:{a % 60:02d}')}–{clock(f'{b // 60:02d}:{b % 60:02d}')}"
        for a, b in spans
    )


def _side(item: dict, key: str) -> Optional[str]:
    entries = item.get("entries") or []
    i = item.get("open_entry_index")
    return entries[i].get(key) if i is not None and i < len(entries) else None


def item_line(item: dict, who: str) -> str:
    parts = [f"• {who}", _day(item["date"]), _sched(item.get("scheduled"))]
    if item["kind"] in (MISSING_OUT, MISSING_OUT_AFTER_BREAK):
        fix = f"out {clock(item['suggested_out'])}" if item.get("suggested_out") else "reply with your out time"
        parts.append(f"In {clock(_side(item, 'in'))}, no out → {fix}")
    elif item["kind"] == MISSING_IN:
        fix = f"in {clock(item['suggested_in'])}" if item.get("suggested_in") else "reply with your in time"
        parts.append(f"Out {clock(_side(item, 'out'))}, no in → {fix}")
    return " · ".join(parts)


def _names(items: list[dict], people: dict[str, dict]) -> dict[str, str]:
    """First name per person, the full name where two share a first name."""
    full = {i["employee_id"]: tp.display_name(i["employee_id"]) for i in items}
    first = {e: n.split()[0] if n else e for e, n in full.items()}
    clash = {f for f in first.values() if list(first.values()).count(f) > 1}
    out = {}
    for e in full:
        name = full[e] if first[e] in clash else first[e]
        member = people.get(e) or {}
        out[e] = f"[@{name}](#user_mention#{member['id']})" if member.get("id") else name
    return out


def compose(today: dt.date, items: list[dict], people: dict[str, dict], footer: str) -> str:
    """The daily post. ``people``: employee_id → {"id": ClickUp user id or None}."""
    who = _names(items, people)
    order = sorted(items, key=lambda i: (i["date"], tp.display_name(i["employee_id"])))
    fills = [item_line(i, who[i["employee_id"]]) for i in order if i["kind"] in FILLS]
    nones = [item_line(i, who[i["employee_id"]]) for i in order if i["kind"] == NO_ENTRY]
    deadline = "Reply in this thread by 9 PM"
    lines = [f"⏰ Missing punches · {_day(today)}",
             deadline + (" — payroll runs Monday morning." if today.weekday() == 6 else ".")]
    if fills:
        lines += ["", "Missing punch — we'll use your scheduled time", *fills]
    if nones:
        lines += ["", "No punches — not paid until you confirm", *nones]
    if footer.strip():
        lines += ["", footer.strip()]
    return "\n".join(lines)


def is_due(now: dt.datetime, *, hour: int, minute: int, enabled: bool) -> bool:
    return enabled and (now.hour, now.minute) >= (hour, minute)


def sweep_due(now: dt.datetime) -> Optional[str]:
    """'payroll' on Monday 06:00–06:59 CT, 'evening' from 21:00 CT, else None."""
    hm = (now.hour, now.minute)
    if now.weekday() == 0 and PAYROLL_SWEEP <= hm < PAYROLL_SWEEP_UNTIL:
        return "payroll"
    if hm >= EVENING_SWEEP:
        return "evening"
    return None


def apply_enabled() -> bool:
    return os.environ.get(APPLY_FLAG) == "1"


# ── BigQuery IO ──────────────────────────────────────────────────


def _q(store: str) -> str:
    return tp._escape(store)


def _gap_item(r: dict) -> dict:
    return {
        "date": r["date"] if isinstance(r["date"], dt.date) else dt.date.fromisoformat(str(r["date"])),
        "employee_id": r["employee_id"],
        "kind": r["kind"],
        "entries": json.loads(r.get("entries_json") or "[]"),
        "scheduled": json.loads(r.get("scheduled_ranges_json") or "[]"),
        "open_entry_index": r.get("open_entry_index"),
        "suggested_in": r.get("suggested_in"),
        "suggested_out": r.get("suggested_out"),
    }


def open_items(store: str, through: dt.date) -> list[dict]:
    """Open gaps dated within LOOKBACK_DAYS before ``through`` (inclusive)."""
    from core.datastore import fq, read_query

    settled = ", ".join(f"'{s}'" for s in SETTLED)
    rows = read_query(f"""
      WITH latest AS (
        SELECT * EXCEPT (rn) FROM (
          SELECT d.*, ROW_NUMBER() OVER (
            PARTITION BY d.store, d.date, d.employee_id ORDER BY d.decided_at DESC) AS rn
          FROM {fq('punch_gap_decisions')} d WHERE d.store = '{_q(store)}'
        ) WHERE rn = 1
      )
      SELECT CAST(g.date AS STRING) AS date, g.employee_id, g.kind, g.entries_json,
             g.scheduled_ranges_json, g.open_entry_index, g.suggested_in, g.suggested_out
      FROM {fq('adp_timecard_gaps')} g
      LEFT JOIN latest l ON l.date = g.date AND l.employee_id = g.employee_id
      WHERE g.store = '{_q(store)}' AND g.kind != 'in_progress'
        AND g.date BETWEEN DATE_SUB(DATE '{through.isoformat()}', INTERVAL {LOOKBACK_DAYS} DAY)
                       AND DATE '{through.isoformat()}'
        AND (l.status IS NULL OR l.status NOT IN ({settled}))
      ORDER BY g.date, g.employee_id
    """)
    return [_gap_item(dict(r)) for r in rows]


def people_for(store: str, team_id: str) -> dict[str, dict]:
    """employee_id → {"id", "username"} for staff whose ADP email is a ClickUp member's."""
    from core.datastore import fq, read_query
    from skills.clickup_chat import list_members

    by_email = {m["email"]: m for m in list_members(team_id) if m.get("email")}
    out: dict[str, dict] = {}
    for r in read_query(
        f"SELECT employee_id, emails_json FROM {fq('employee_contacts')} WHERE store = '{_q(store)}'"
    ):
        for e in json.loads(r.get("emails_json") or "[]"):
            if e in by_email:
                out[r["employee_id"]] = by_email[e]
                break
    return out


def ensure_default_config(store: str, *, updated_by: str) -> dict:
    from core.datastore import fq, load_rows, read_query

    rows = read_query(
        f"SELECT * FROM {fq('automations')}"
        f" WHERE store = '{_q(store)}' AND automation_id = '{AUTOMATION_ID}' LIMIT 1"
    )
    if rows:
        return dict(rows[0])
    row = {
        "store": store,
        "automation_id": AUTOMATION_ID,
        "enabled": True,
        "days_of_week": json.dumps(list(range(7))),
        "hour_local": DEFAULT_HOUR,
        "minute_local": 0,
        "timezone": tp.DEFAULT_TZ,
        "destination": "channel",
        "channel_id": DEFAULT_CHANNEL_ID,
        "dm_user_id": tp.DEFAULT_DM_USER_ID,
        "workspace_id": tp.DEFAULT_WORKSPACE_ID,
        "template": DEFAULT_FOOTER,
        "updated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "updated_by": updated_by,
    }
    load_rows("automations", [row], merge_keys=["store", "automation_id"],
              column_bq_types={"updated_at": "TIMESTAMP", "enabled": "BOOL"})
    logger.info("punch_check: seeded default config store=%s", store)
    return row


def _record_items(store: str, today: dt.date, message_id: str, channel_id: str,
                  items: list[dict], people: dict[str, dict]) -> None:
    from core.datastore import load_rows

    now = dt.datetime.now(tz=dt.timezone.utc).isoformat()
    load_rows("punch_check_items", [{
        "store": store, "post_date_ct": today.isoformat(), "message_id": message_id,
        "channel_id": channel_id, "date": i["date"].isoformat(), "employee_id": i["employee_id"],
        "kind": i["kind"], "clickup_user_id": (people.get(i["employee_id"]) or {}).get("id"),
        "posted_at": now,
    } for i in items], merge_keys=None,
        column_bq_types={"post_date_ct": "DATE", "date": "DATE", "posted_at": "TIMESTAMP",
                         "clickup_user_id": "STRING"})


def _insert_decisions(rows: list[dict]) -> None:
    from core.datastore import load_rows

    load_rows("punch_gap_decisions", rows, merge_keys=None, column_bq_types={
        "date": "DATE", "decided_at": "TIMESTAMP", "open_entry_index": "INT64",
        "in_time": "STRING", "out_time": "STRING", "note": "STRING",
    })


def _upsert_replies(rows: list[dict]) -> None:
    from core.datastore import load_rows

    load_rows("punch_check_replies", rows, merge_keys=["store", "reply_id"], column_bq_types={
        "processed_at": "TIMESTAMP", "reacted_at": "TIMESTAMP", "employee_id": "STRING",
        "decision_ids_json": "STRING", "detail": "STRING",
    })


# ── Daily post ───────────────────────────────────────────────────


def post_daily(cfg: dict, store: str, now: dt.datetime, *, dry_run: bool = False,
               force: bool = False, to_dm: bool = False, trigger: str = "scheduler",
               updated_by: str = "punch_check") -> dict[str, Any]:
    today = now.date()
    hour = int(cfg.get("hour_local") if cfg.get("hour_local") is not None else DEFAULT_HOUR)
    minute = int(cfg.get("minute_local") or 0)
    enabled = bool(cfg.get("enabled"))
    if not force and not is_due(now, hour=hour, minute=minute, enabled=enabled):
        return {"status": "skipped", "reason": f"skip: enabled={enabled} now={now:%H:%M}"}
    if not (dry_run or to_dm) and tp.already_posted(store, AUTOMATION_ID, today):
        return {"status": "skipped", "reason": f"skip: already posted for {today}"}

    items = open_items(store, today - dt.timedelta(days=1))
    if not items:
        return {"status": "skipped", "reason": "nothing open"}
    team = str(cfg.get("workspace_id") or tp.DEFAULT_WORKSPACE_ID)
    people = people_for(store, team)
    content = compose(today, items, people, str(cfg.get("template") or DEFAULT_FOOTER))
    summary = {"items": len(items), "tagged": sum(1 for i in items if i["employee_id"] in people),
               "untagged": sorted({i["employee_id"] for i in items if i["employee_id"] not in people})}
    if dry_run:
        return {"status": "dry_run", "content": content, **summary}

    from core.datastore import ensure_schema
    from skills.clickup_chat import ensure_dm_channel, post_message

    ensure_schema()
    if to_dm:
        ch = ensure_dm_channel([str(cfg.get("dm_user_id") or tp.DEFAULT_DM_USER_ID)], team_id=team)
        created = post_message(str(ch["id"]), content, team_id=team)
        return {"status": "dm", "message_id": str(created.get("id") or ""), "content": content, **summary}
    channel_id = str(cfg.get("channel_id") or DEFAULT_CHANNEL_ID)
    created = post_message(channel_id, content, team_id=team)
    message_id = str(created.get("id") or "")
    tp.record_post(store=store, post_date_ct=today, destination="channel", channel_id=channel_id,
                   message_id=message_id or None, content=content, dry_run=False, trigger=trigger,
                   updated_by=updated_by, automation_id=AUTOMATION_ID)
    _record_items(store, today, message_id, channel_id, items, people)
    logger.info("punch_check: posted message_id=%s items=%d untagged=%s",
                message_id, len(items), summary["untagged"])
    return {"status": "posted", "message_id": message_id, "content": content, **summary}


# ── Reply sweep ──────────────────────────────────────────────────


def _reply_item(item: dict):
    from skills.bhaga_labor.punch_reply import ReplyItem

    return ReplyItem(
        date=item["date"], kind=item["kind"],
        entries=tuple((e.get("in"), e.get("out")) for e in item.get("entries") or []),
        open_entry_index=item.get("open_entry_index"),
        scheduled=tuple(tuple(r) for r in item.get("scheduled") or []),
        suggested_in=item.get("suggested_in"), suggested_out=item.get("suggested_out"),
    )


def decision_row(store: str, item: dict, *, action: str, in_time: Optional[str],
                 out_time: Optional[str], decided_by: str, note: Optional[str]) -> dict:
    return {
        "decision_id": str(uuid.uuid4()), "store": store, "date": item["date"].isoformat(),
        "employee_id": item["employee_id"], "action": action, "in_time": in_time,
        "out_time": out_time, "open_entry_index": item.get("open_entry_index"), "note": note,
        "status": "rejected" if action == "reject" else "recorded", "decided_by": decided_by,
        "decided_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
    }


def read_reply(store: str, reply: dict, message_id: str, posted: dict[str, list[dict]],
               open_by_key: dict[tuple, dict], by_user: dict[str, str]) -> tuple[dict, list[dict]]:
    """(punch_check_replies row, decision rows) for one thread reply. Pure apart from uuid/clock.

    ``posted``: employee_id → items listed in this post; ``open_by_key``:
    (date, employee_id) → still-open gap; ``by_user``: ClickUp user id → employee_id.
    """
    from skills.bhaga_labor.punch_reply import parse_reply

    text = str(reply.get("content") or "")
    author = str(reply.get("user_id") or (reply.get("user") or {}).get("id") or "")
    row = {"store": store, "reply_id": str(reply["id"]), "message_id": message_id,
           "author_id": author, "employee_id": None, "text": text[:1000], "detail": None,
           "decision_ids_json": None,
           "processed_at": dt.datetime.now(tz=dt.timezone.utc).isoformat()}
    emp = by_user.get(author)
    if not emp:
        return {**row, "outcome": "unknown_author"}, []
    row["employee_id"] = emp
    candidates = [open_by_key[(i["date"], emp)] for i in posted.get(emp, [])
                  if (i["date"], emp) in open_by_key]
    if not candidates:
        return {**row, "outcome": "nothing_open"}, []
    resolutions = parse_reply(text, [_reply_item(c) for c in candidates])
    if not resolutions:
        return {**row, "outcome": "not_understood"}, []
    by_date = {c["date"]: c for c in candidates}
    decisions = [
        decision_row(store, by_date[r.date], action=r.action, in_time=r.in_time,
                     out_time=r.out_time, decided_by=f"{STAFF_PREFIX}{tp.display_name(emp)}",
                     note=f"ClickUp reply {row['reply_id']}: {text[:200]}")
        for r in resolutions
    ]
    for r in resolutions:
        open_by_key.pop((r.date, emp), None)
    return {**row, "outcome": "recorded",
            "decision_ids_json": json.dumps([d["decision_id"] for d in decisions])}, decisions


def payroll_defaults(store: str, open_by_key: dict[tuple, dict]) -> list[dict]:
    """Accept the scheduled time on every open missing clock-in/out that has one."""
    out = []
    for item in open_by_key.values():
        if item["kind"] in (MISSING_OUT, MISSING_OUT_AFTER_BREAK) and item.get("suggested_out"):
            out.append(decision_row(store, item, action="accept", in_time=None,
                                    out_time=item["suggested_out"], decided_by=PAYROLL_DEFAULT_BY,
                                    note="No reply before payroll — scheduled time"))
        elif item["kind"] == MISSING_IN and item.get("suggested_in") and _side(item, "out"):
            out.append(decision_row(store, item, action="accept", in_time=item["suggested_in"],
                                    out_time=_side(item, "out"), decided_by=PAYROLL_DEFAULT_BY,
                                    note="No reply before payroll — scheduled time"))
    return out


def _posted_items(store: str, since: dt.date) -> dict[str, dict[str, list[dict]]]:
    """message_id → employee_id → items listed in that post."""
    from core.datastore import fq, read_query

    out: dict[str, dict[str, list[dict]]] = {}
    for r in read_query(
        f"SELECT message_id, CAST(date AS STRING) AS date, employee_id, kind"
        f" FROM {fq('punch_check_items')}"
        f" WHERE store = '{_q(store)}' AND post_date_ct >= DATE '{since.isoformat()}'"
    ):
        out.setdefault(r["message_id"], {}).setdefault(r["employee_id"], []).append(
            {"date": dt.date.fromisoformat(str(r["date"])), "kind": r["kind"]})
    return out


def _processed_reply_ids(store: str, message_ids: list[str]) -> set[str]:
    from core.datastore import fq, read_query

    if not message_ids:
        return set()
    ids = ", ".join(f"'{tp._escape(m)}'" for m in message_ids)
    return {r["reply_id"] for r in read_query(
        f"SELECT reply_id FROM {fq('punch_check_replies')}"
        f" WHERE store = '{_q(store)}' AND message_id IN ({ids})")}


def sweep_replies(cfg: dict, store: str, now: dt.datetime, *, kind: str,
                  dry_run: bool = False) -> dict[str, Any]:
    """Read new thread replies into decisions; on the payroll sweep also default the rest."""
    from skills.clickup_chat import fetch_replies

    today = now.date()
    team = str(cfg.get("workspace_id") or tp.DEFAULT_WORKSPACE_ID)
    posts = _posted_items(store, today - dt.timedelta(days=THREAD_LOOKBACK_DAYS))
    done_ids = _processed_reply_ids(store, list(posts))
    open_by_key = {(i["date"], i["employee_id"]): i for i in open_items(store, today)}
    by_user = {m["id"]: emp for emp, m in people_for(store, team).items()}

    replies_out: list[dict] = []
    decisions: list[dict] = []
    for message_id, posted in posts.items():
        try:
            replies = fetch_replies(message_id, team_id=team)
        except Exception as exc:  # noqa: BLE001 — one thread must not stop the rest
            logger.error("BREADCRUMB punch_check replies_failed message_id=%s err=%s", message_id, exc)
            continue
        for reply in replies:
            if str(reply.get("id")) in done_ids:
                continue
            row, ds = read_reply(store, reply, message_id, posted, open_by_key, by_user)
            replies_out.append(row)
            decisions.extend(ds)
    defaults = payroll_defaults(store, open_by_key) if kind == "payroll" and apply_enabled() else []
    summary = {
        "kind": kind, "replies": len(replies_out), "decisions": len(decisions),
        "defaults": len(defaults),
        "outcomes": {o: sum(1 for r in replies_out if r["outcome"] == o)
                     for o in sorted({r["outcome"] for r in replies_out})},
    }
    if dry_run:
        return {**summary, "status": "dry_run", "reply_rows": replies_out,
                "decision_rows": decisions + defaults}
    if decisions or defaults:
        _insert_decisions(decisions + defaults)
    if replies_out:
        _upsert_replies(replies_out)
    logger.info("punch_check sweep result=%s", summary)
    return {**summary, "status": "swept"}


# ── Apply + ✅ ───────────────────────────────────────────────────


def apply_pending(store: str, trigger_apply: Callable[[list[str]], bool]) -> dict[str, Any]:
    """Claim staff/auto decisions still ``recorded`` and start the apply job.

    If the job cannot start (another execution is running) the claim is
    released back to ``recorded`` and the next tick tries again — that only
    repeats *starting* the job; a written punch is never retried.
    """
    from core.datastore import _param_config, fq, get_client

    client = get_client()
    # The claim token keeps rows the console claimed at the same moment out of this batch.
    claim = f"punch-check:{uuid.uuid4()}"
    mine = _param_config([("store", "STRING", store), ("claim", "STRING", claim)])
    client.query(f"""
      UPDATE {fq('punch_gap_decisions')} d
      SET status = 'applying', error = NULL, execution_name = @claim
      WHERE d.store = @store AND d.status = 'recorded' AND d.action IN ('accept', 'edit')
        AND (d.decided_by LIKE @staff OR d.decided_by = @auto)
        AND d.decided_at = (
          SELECT MAX(x.decided_at) FROM {fq('punch_gap_decisions')} x
          WHERE x.store = d.store AND x.date = d.date AND x.employee_id = d.employee_id)
    """, job_config=_param_config([("store", "STRING", store), ("claim", "STRING", claim),
                                   ("staff", "STRING", f"{STAFF_PREFIX}%"),
                                   ("auto", "STRING", PAYROLL_DEFAULT_BY)])).result()
    ids = [r["decision_id"] for r in client.query(
        f"SELECT decision_id FROM {fq('punch_gap_decisions')}"
        f" WHERE store = @store AND status = 'applying' AND execution_name = @claim",
        job_config=mine).result()]
    if not ids:
        return {"status": "nothing_to_apply"}
    if trigger_apply(ids):
        logger.info("punch_check: apply job started for %d decision(s)", len(ids))
        return {"status": "started", "decisions": len(ids)}
    client.query(
        f"UPDATE {fq('punch_gap_decisions')} SET status = 'recorded', execution_name = NULL"
        f" WHERE store = @store AND status = 'applying' AND execution_name = @claim",
        job_config=mine).result()
    logger.warning("BREADCRUMB punch_check apply_job_busy decisions=%d — retry next tick", len(ids))
    return {"status": "busy", "decisions": len(ids)}


def reply_outcome(statuses: list[Optional[str]]) -> Optional[str]:
    """'done' when every decision is written/dismissed, 'failed' if any failed, else None (wait)."""
    if statuses and all(s in DONE for s in statuses):
        return "done"
    if any(s == "failed" for s in statuses):
        return "failed"
    return None


def react_done(store: str, team_id: str) -> dict[str, Any]:
    """✅ every reply whose decisions all landed; mark failed ones (they repost tomorrow)."""
    from core.datastore import fq, read_query
    from skills.clickup_chat import add_reaction

    rows = read_query(f"""
      SELECT r.reply_id, ARRAY_AGG(d.status IGNORE NULLS) AS statuses
      FROM {fq('punch_check_replies')} r,
           UNNEST(JSON_VALUE_ARRAY(r.decision_ids_json)) AS did
      LEFT JOIN {fq('punch_gap_decisions')} d ON d.decision_id = did
      WHERE r.store = '{_q(store)}' AND r.outcome = 'recorded'
        AND r.processed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 DAY)
      GROUP BY r.reply_id
    """)
    updates, reacted = [], 0
    for r in rows:
        outcome = reply_outcome(list(r.get("statuses") or []))
        if outcome is None:
            continue
        row = {"store": store, "reply_id": r["reply_id"], "outcome": outcome}
        if outcome == "done":
            try:
                add_reaction(r["reply_id"], team_id=team_id)
                row["reacted_at"] = dt.datetime.now(tz=dt.timezone.utc).isoformat()
                reacted += 1
            except Exception as exc:  # noqa: BLE001 — stays 'recorded'; next tick retries the ✅
                logger.error("BREADCRUMB punch_check react_failed reply_id=%s err=%s", r["reply_id"], exc)
                continue
        updates.append(row)
    if updates:
        _upsert_replies(updates)
    return {"reacted": reacted, "failed": sum(1 for u in updates if u["outcome"] == "failed")}


# ── Tick ─────────────────────────────────────────────────────────


def run_tick(*, store: str = tp.DEFAULT_STORE, now: Optional[dt.datetime] = None,
             trigger_apply: Optional[Callable[[list[str]], bool]] = None,
             updated_by: str = "punch_check") -> dict[str, Any]:
    """One 15-minute tick: daily post, due reply sweep, apply, ✅."""
    cfg = ensure_default_config(store, updated_by=updated_by)
    tz = str(cfg.get("timezone") or tp.DEFAULT_TZ)
    now = (now or dt.datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz))
    team = str(cfg.get("workspace_id") or tp.DEFAULT_WORKSPACE_ID)
    out: dict[str, Any] = {"post": post_daily(cfg, store, now, updated_by=updated_by)}
    out["post"].pop("content", None)

    kind = sweep_due(now)
    ledger = f"{AUTOMATION_ID}:sweep-{kind}"
    if kind and bool(cfg.get("enabled")) and not tp.already_posted(store, ledger, now.date()):
        out["sweep"] = sweep_replies(cfg, store, now, kind=kind)
        tp.record_post(store=store, post_date_ct=now.date(), destination="none", channel_id=None,
                       message_id=None, content=json.dumps(out["sweep"]), dry_run=False,
                       trigger="scheduler", updated_by=updated_by, automation_id=ledger)
    if apply_enabled() and trigger_apply is not None:
        out["apply"] = apply_pending(store, trigger_apply)
    out["react"] = react_done(store, team)
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--store", default=tp.DEFAULT_STORE)
    p.add_argument("--dry-run", action="store_true", help="compose today's post from BQ; send nothing")
    p.add_argument("--to-dm", action="store_true",
                   help="send today's post to the operator's ClickUp DM (demo; not recorded)")
    p.add_argument("--sweep-dry-run", action="store_true",
                   help="read thread replies and print what would be decided; write nothing")
    p.add_argument("--date", type=dt.date.fromisoformat, help="compose as if today were this date")
    a = p.parse_args(argv)
    tz = ZoneInfo(tp.DEFAULT_TZ)
    now = dt.datetime.combine(a.date, dt.time(12), tzinfo=tz) if a.date else dt.datetime.now(tz)
    cfg = ensure_default_config(a.store, updated_by="cli")
    if a.sweep_dry_run:
        result = sweep_replies(cfg, a.store, now, kind="evening", dry_run=True)
        print(json.dumps(result, default=str, indent=2))
        return 0
    result = post_daily(cfg, a.store, now, dry_run=a.dry_run, force=True, to_dm=a.to_dm,
                        trigger="once", updated_by="cli")
    print(json.dumps({k: v for k, v in result.items() if k != "content"}, default=str))
    if result.get("content"):
        print("---")
        print(result["content"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
