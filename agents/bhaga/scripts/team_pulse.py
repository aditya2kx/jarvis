#!/usr/bin/env python3
"""Team pulse automation — ClickUp motivating leaderboard posts (Issue #216).

Composable, day-gated, idempotent. Default destination is a ClickUp DM to the
operator; promote to the group channel via Operator Console when ready.

CLI:
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.team_pulse --dry-run
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.team_pulse --once
  BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.team_pulse   # scheduler
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from collections import defaultdict
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

AUTOMATION_ID = "team-pulse"
DEFAULT_STORE = "palmetto"
DEFAULT_WORKSPACE_ID = "9017956545"
DEFAULT_CHANNEL_ID = "8cr6661-737"  # running-austin-palmetto
DEFAULT_DM_USER_ID = "198109189"  # Aditya Parikh
DEFAULT_TZ = "America/Chicago"
# Python weekday: Mon=0 … Sun=6 → Tue/Thu/Sun
DEFAULT_DAYS = [1, 3, 6]

DEFAULT_TEMPLATE = """Good Morning Team ! Sharing {pay_cycle}'s leaderboard based of Google Review Bonus.

{leaderboard}

Keep the momentum going...!! One team, one fight.

There would be more such incentives/challenges program rolled out soon.
"""

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def apply_pay_cycle_wording(template: str, *, is_current: bool = True,
                            period_start: str | None = None,
                            period_end: str | None = None) -> str:
    """Fill ``{pay_cycle}``; rewrite legacy 'current pay cycle' when not current.

    Scheduled posts always use ``is_current=True`` (open period).
    """
    if is_current:
        label = "current pay cycle"
    elif period_start and period_end:
        # Match TS formatPayCycleDate (en-US short month).
        def _fmt(iso: str) -> str:
            dt = datetime.date.fromisoformat(iso)
            return f"{dt.strftime('%b')} {dt.day}"
        label = f"the {_fmt(period_start)} – {_fmt(period_end)} pay cycle"
    elif period_start:
        dt = datetime.date.fromisoformat(period_start)
        label = f"the {dt.strftime('%b')} {dt.day} pay cycle"
    else:
        label = "current pay cycle"

    t = template.replace("{pay_cycle}", label) if "{pay_cycle}" in template else template
    if not is_current:
        import re
        t = re.sub(r"\bcurrent pay cycle's\b", f"{label}'s", t, flags=re.I)
        t = re.sub(r"\bcurrent pay cycle\b", label, t, flags=re.I)
    return t


def compose_message(template: str, leaderboard_md: str,
                    *, is_current: bool = True,
                    period_start: str | None = None,
                    period_end: str | None = None) -> str:
    """Fill ``{pay_cycle}`` + ``{leaderboard}`` (tolerate missing placeholder)."""
    filled = apply_pay_cycle_wording(
        template,
        is_current=is_current,
        period_start=period_start,
        period_end=period_end,
    )
    if "{leaderboard}" in filled:
        return filled.replace("{leaderboard}", leaderboard_md.strip()).strip()
    if not leaderboard_md.strip():
        return filled.strip()
    return f"{filled.strip()}\n\n{leaderboard_md.strip()}"


def accept_varied_copy(text: str, leaderboard_md: str) -> tuple[str, bool]:
    """Pure gate: exactly one verbatim leaderboard; reject multi-draft ``---``.

    Returns ``(text, True)`` when acceptable, else ``("", False)``.
    """
    lb = (leaderboard_md or "").strip()
    t = (text or "").strip()
    if not t or not lb:
        return "", False
    if t.count(lb) != 1:
        return "", False
    # Draft separators like the 2026-08-08 triple-variation ClickUp post.
    for line in t.splitlines():
        if line.strip() == "---":
            return "", False
    return t, True


def vary_motivational_copy(
    message: str,
    leaderboard_md: str,
    *,
    token: str | None = None,
) -> tuple[str, bool]:
    """Gemini paraphrase of greeting/closers; leaderboard must stay verbatim.

    Returns ``(text, varied)``. Falls back to ``message`` if no token / API
    failure / leaderboard not preserved / multi-draft response.
    """
    import os
    import urllib.error
    import urllib.request

    lb = leaderboard_md.strip()
    tok = (token if token is not None else os.environ.get("GEMINI_TOKEN", "")).strip()
    if not tok or not lb:
        return message, False

    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={tok}"
    )
    prompt = (
        "You rewrite a short ClickUp team chat message for a smoothie shop.\n"
        "Rules:\n"
        "1. Keep the LEADERBOARD block below EXACTLY character-for-character — "
        "do not change names, dollars, bullets, or markdown.\n"
        "2. Vary only the greeting / intro and the closing motivational lines "
        "(one-team energy, keep momentum, collaborative) so each post feels fresh.\n"
        "3. Stay short, warm, professional. No emoji overload. "
        "No new employee names or dollar amounts.\n"
        "4. Return EXACTLY ONE full message markdown — no alternatives, "
        "no numbered options, no --- separators between drafts.\n\n"
        f"LEADERBOARD (must appear verbatim):\n{lb}\n\n"
        f"CURRENT MESSAGE:\n{message}"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9},
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
        text = (
            ((data.get("candidates") or [{}])[0].get("content") or {})
            .get("parts") or [{}]
        )[0].get("text") or ""
        accepted, ok = accept_varied_copy(text, lb)
        if not ok:
            logger.warning(
                "team_pulse: Gemini multi-draft or drop/alter leaderboard — using template"
            )
            return message, False
        return accepted, True
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        logger.warning("team_pulse: Gemini vary failed: %s — using template", e)
        return message, False


def display_name(employee: str) -> str:
    """Turn 'Last, First' into 'First Last' when possible."""
    raw = (employee or "").strip()
    if ", " in raw:
        last, first = raw.split(", ", 1)
        return f"{first.strip()} {last.strip()}".strip()
    return raw


def format_money(amount: float | int | str) -> str:
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    if abs(v - round(v)) < 1e-9:
        return f"${int(round(v))}"
    return f"${v:.2f}"


def format_leaderboard(rows: list[dict]) -> str:
    """Group employees by total_bonus descending; skip zero/empty."""
    by_amount: dict[float, list[str]] = defaultdict(list)
    for r in rows:
        try:
            amt = float(r.get("total_bonus") or 0)
        except (TypeError, ValueError):
            continue
        if amt <= 0:
            continue
        name = display_name(str(r.get("employee") or ""))
        if not name:
            continue
        by_amount[amt].append(name)

    if not by_amount:
        return "_No review bonuses credited in the current open pay period yet._"

    lines: list[str] = []
    amounts = sorted(by_amount.keys(), reverse=True)
    for i, amt in enumerate(amounts):
        names = sorted(by_amount[amt])
        money = format_money(amt)
        top = i == 0
        if len(names) == 1:
            verb = f"leading with {money}" if top else f"at {money}"
            lines.append(f"*   **{names[0]}** {verb}.")
        elif len(names) == 2:
            verb = f"leading with {money} each" if top else f"at {money} each"
            lines.append(f"*   **{names[0]}** and **{names[1]}** {verb}.")
        else:
            head = ", ".join(f"**{n}**" for n in names[:-1])
            verb = f"leading with {money} each" if top else f"at {money} each"
            lines.append(f"*   {head}, and **{names[-1]}** {verb}.")
    return "\n".join(lines)


def should_run_today(
    days_of_week: list[int],
    *,
    today_weekday: int,
    enabled: bool,
) -> bool:
    if not enabled:
        return False
    return today_weekday in set(days_of_week)


def parse_days(raw: Any) -> list[int]:
    if isinstance(raw, list):
        return [int(x) for x in raw]
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return list(DEFAULT_DAYS)
        return [int(x) for x in json.loads(raw)]
    return list(DEFAULT_DAYS)


def cadence_summary(days: list[int], hour: int, minute: int, tz: str) -> str:
    labels = " · ".join(DAY_LABELS[d] for d in sorted(days) if 0 <= d <= 6)
    return f"{labels} · {hour:02d}:{minute:02d} {tz}"


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def ensure_default_config(store: str = DEFAULT_STORE, *, updated_by: str = "team_pulse") -> dict:
    """Return existing config or insert defaults and return them."""
    from core.datastore import fq, load_rows, read_query

    rows = read_query(
        f"SELECT * FROM {fq('automations')}"
        f" WHERE store = '{_escape(store)}' AND automation_id = '{AUTOMATION_ID}'"
        f" LIMIT 1"
    )
    if rows:
        return dict(rows[0])

    row = {
        "store": store,
        "automation_id": AUTOMATION_ID,
        "enabled": True,
        "days_of_week": json.dumps(DEFAULT_DAYS),
        "hour_local": 8,
        "minute_local": 0,
        "timezone": DEFAULT_TZ,
        "destination": "dm",
        "channel_id": DEFAULT_CHANNEL_ID,
        "dm_user_id": DEFAULT_DM_USER_ID,
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "template": DEFAULT_TEMPLATE,
        "updated_at": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        "updated_by": updated_by,
    }
    load_rows(
        "automations",
        [row],
        merge_keys=["store", "automation_id"],
        column_bq_types={"updated_at": "TIMESTAMP", "enabled": "BOOL"},
    )
    logger.info("team_pulse: seeded default config store=%s", store)
    return row


def fetch_open_leaderboard(store: str = DEFAULT_STORE) -> list[dict]:
    """Latest open period's bonus rows (by period_start), any total_bonus."""
    from core.datastore import fq, read_query

    # Pick the most recent open period_start, then all rows in that period.
    rows = read_query(
        f"""
        WITH open_periods AS (
          SELECT period_start, period_end
          FROM {fq('model_review_bonus_period')}
          WHERE is_open = TRUE
          ORDER BY period_start DESC
          LIMIT 1
        )
        SELECT m.employee, m.total_bonus, m.period_start, m.period_end
        FROM {fq('model_review_bonus_period')} m
        JOIN open_periods o
          ON m.period_start = o.period_start AND m.period_end = o.period_end
        WHERE m.is_open = TRUE
        ORDER BY m.total_bonus DESC, m.employee
        """
    )
    _ = store  # single-store dataset today
    return [dict(r) for r in rows]


def already_posted(
    store: str,
    automation_id: str,
    post_date_ct: datetime.date,
) -> bool:
    from core.datastore import fq, read_query

    rows = read_query(
        f"SELECT message_id FROM {fq('automation_posts')}"
        f" WHERE store = '{_escape(store)}'"
        f" AND automation_id = '{_escape(automation_id)}'"
        f" AND post_date_ct = '{post_date_ct.isoformat()}'"
        f" AND dry_run = FALSE"
        f" LIMIT 1"
    )
    return bool(rows)


def record_post(
    *,
    store: str,
    post_date_ct: datetime.date,
    destination: str,
    channel_id: str | None,
    message_id: str | None,
    content: str,
    dry_run: bool,
    trigger: str,
    updated_by: str,
) -> None:
    from core.datastore import load_rows

    load_rows(
        "automation_posts",
        [{
            "store": store,
            "automation_id": AUTOMATION_ID,
            "post_date_ct": post_date_ct.isoformat(),
            "posted_at": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
            "destination": destination,
            "channel_id": channel_id,
            "message_id": message_id,
            "content": content,
            "dry_run": dry_run,
            "trigger": trigger,
            "updated_by": updated_by,
        }],
        merge_keys=None,
        column_bq_types={
            "posted_at": "TIMESTAMP",
            "post_date_ct": "DATE",
            "dry_run": "BOOL",
        },
    )


def resolve_target_channel(cfg: dict) -> tuple[str, str]:
    """Return (destination, channel_id) ready for post_message."""
    from skills.clickup_chat import ensure_dm_channel

    dest = (cfg.get("destination") or "dm").strip().lower()
    workspace = str(cfg.get("workspace_id") or DEFAULT_WORKSPACE_ID)
    if dest == "channel":
        cid = str(cfg.get("channel_id") or DEFAULT_CHANNEL_ID)
        return "channel", cid
    dm_user = str(cfg.get("dm_user_id") or DEFAULT_DM_USER_ID)
    ch = ensure_dm_channel([dm_user], team_id=workspace)
    return "dm", str(ch["id"])


def run_team_pulse(
    *,
    store: str = DEFAULT_STORE,
    dry_run: bool = False,
    force: bool = False,
    allow_dup: bool = False,
    trigger: str = "scheduler",
    updated_by: str = "team_pulse",
) -> dict[str, Any]:
    """Run one team-pulse cycle. Returns a result dict (status + content).

    ``force`` skips the day-of-week / enabled gate (Post once / --once).
    ``allow_dup`` permits a second real post on the same CT date.
    """
    cfg = ensure_default_config(store, updated_by=updated_by)
    tz_name = str(cfg.get("timezone") or DEFAULT_TZ)
    now_local = datetime.datetime.now(ZoneInfo(tz_name))
    post_date = now_local.date()
    days = parse_days(cfg.get("days_of_week"))
    enabled = bool(cfg.get("enabled"))

    if not force and not should_run_today(
        days, today_weekday=now_local.weekday(), enabled=enabled
    ):
        msg = (
            f"skip: enabled={enabled} weekday={now_local.weekday()} "
            f"days={days} date={post_date}"
        )
        logger.info("team_pulse: %s", msg)
        return {"status": "skipped", "reason": msg, "post_date_ct": post_date.isoformat()}

    if not dry_run and not allow_dup and already_posted(store, AUTOMATION_ID, post_date):
        msg = f"skip: already posted for {post_date}"
        logger.info("team_pulse: %s", msg)
        return {"status": "skipped", "reason": msg, "post_date_ct": post_date.isoformat()}

    rows = fetch_open_leaderboard(store)
    leaderboard = format_leaderboard(rows)
    base = compose_message(str(cfg.get("template") or DEFAULT_TEMPLATE), leaderboard)
    content, varied = vary_motivational_copy(base, leaderboard)

    if dry_run:
        print(content)
        return {
            "status": "dry_run",
            "content": content,
            "varied": varied,
            "post_date_ct": post_date.isoformat(),
            "leaderboard_rows": len(rows),
        }

    from skills.clickup_chat import post_message

    dest, channel_id = resolve_target_channel(cfg)
    workspace = str(cfg.get("workspace_id") or DEFAULT_WORKSPACE_ID)
    created = post_message(channel_id, content, team_id=workspace)
    message_id = str(created.get("id") or "")
    record_post(
        store=store,
        post_date_ct=post_date,
        destination=dest,
        channel_id=channel_id,
        message_id=message_id or None,
        content=content,
        dry_run=False,
        trigger=trigger,
        updated_by=updated_by,
    )
    logger.info(
        "team_pulse: posted destination=%s channel=%s message_id=%s",
        dest, channel_id, message_id,
    )
    return {
        "status": "posted",
        "destination": dest,
        "channel_id": channel_id,
        "message_id": message_id,
        "content": content,
        "post_date_ct": post_date.isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store", default=DEFAULT_STORE)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--once",
        action="store_true",
        help="Force a post ignoring day-of-week gate (still idempotent unless --force-dup).",
    )
    p.add_argument(
        "--force-dup",
        action="store_true",
        help="Allow a second real post on the same CT date (ops escape hatch).",
    )
    p.add_argument("--trigger", default=None, help="Override trigger label")
    args = p.parse_args(argv)

    trigger = args.trigger or (
        "once" if args.once else ("preview" if args.dry_run else "scheduler")
    )
    result = run_team_pulse(
        store=args.store,
        dry_run=args.dry_run,
        force=bool(args.once),
        allow_dup=bool(args.force_dup),
        trigger=trigger,
        updated_by="cli",
    )
    print(json.dumps({k: v for k, v in result.items() if k != "content"}, default=str))
    if result.get("status") in ("posted", "dry_run") and result.get("content"):
        if not args.dry_run:
            print("---")
            print(result["content"][:500])
    return 0 if result.get("status") != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
