"""Push console draft shifts into ADP Team Schedule — Issue #337.

Two operator-confirmed modes, each started by one console click:

  drafts   python3 -m agents.bhaga.scripts.adp_schedule_write --store palmetto --push-id <id>
           Creates every ``labor_schedule_pushes`` row of that push (status='queued')
           as an ADP *draft* shift (assigned) or draft open shift (employee NULL).
           Rows whose row_key is already drafted/published are marked 'skipped'.
  publish  ... --publish --week-start 2026-09-28
           Clicks ADP "Publish drafts" for that week and flips the week's 'drafted'
           rows to 'published'. The operator announces open shifts themselves.

``--dry-run`` walks each ADP wizard to its final step and backs out; BQ is
left untouched. Cloud Run: daily_refresh early-exits here when
``BHAGA_ADP_SCHEDULE_WRITE`` is set (see that module).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from skills.adp_run_automation import schedule_write_backend as wb


def _bq():
    from google.cloud import bigquery

    from core.datastore import fq, get_client

    client = get_client()
    if client is None:
        raise RuntimeError("BigQuery datastore is not enabled (BHAGA_DATASTORE=bigquery)")
    return client, fq("labor_schedule_pushes"), bigquery


def _query(sql: str, params: list[tuple[str, str, object]]) -> list[dict]:
    client, table, bigquery = _bq()
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter(n, t, v) for n, t, v in params]
    )
    return [dict(r) for r in client.query(sql.replace("{T}", table), job_config=cfg).result()]


def _set_status(row_key: str, push_id: str, status: str, error: str | None = None) -> None:
    _query(
        "UPDATE {T} SET status = @status, error = @error, updated_at = CURRENT_TIMESTAMP()"
        " WHERE push_id = @push AND row_key = @key",
        [("status", "STRING", status), ("error", "STRING", error),
         ("push", "STRING", push_id), ("key", "STRING", row_key)],
    )


def week_start_of(date: dt.date) -> dt.date:
    return date - dt.timedelta(days=date.weekday())


def _same_person(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return a == b or a.startswith(b + " ") or b.startswith(a + " ")


def published_row_keys(rows: list[dict], states: list[dict]) -> list[str]:
    """Drafted rows whose shift ADP now shows without the draft tag.

    An assigned row also matches an open shift at the same times: ADP unassigns a
    saved shift when that person's unavailability is approved.
    """
    live = [s for s in states if not s["draft"]]
    out = []
    for r in rows:
        if any(
            s["date"] == str(r["date"])
            and s["start_min"] == r["start_min"]
            and s["end_min"] == r["end_min"]
            and (s["employee"] is None or (r["employee"] and _same_person(s["employee"], r["employee"])))
            for s in live
        ):
            out.append(r["row_key"])
    return out


def reconcile_published(store: str, states: list[dict]) -> int:
    """Mark drafted rows 'published' when ADP shows them published (e.g. published in ADP)."""
    dates = sorted({s["date"] for s in states})
    if not dates:
        return 0
    latest = _query(
        "SELECT row_key, status, CAST(date AS STRING) AS date, employee, start_min, end_min FROM {T}"
        " WHERE store = @store AND date BETWEEN @lo AND @hi"
        " QUALIFY ROW_NUMBER() OVER (PARTITION BY row_key ORDER BY updated_at DESC) = 1",
        [("store", "STRING", store), ("lo", "DATE", dates[0]), ("hi", "DATE", dates[-1])],
    )
    keys = published_row_keys([r for r in latest if r["status"] == "drafted"], states)
    if keys:
        _query(
            "UPDATE {T} SET status = 'published', error = NULL, updated_at = CURRENT_TIMESTAMP()"
            " WHERE store = @store AND status = 'drafted' AND row_key IN UNNEST(SPLIT(@keys, '\\n'))",
            [("store", "STRING", store), ("keys", "STRING", "\n".join(keys))],
        )
    print(f"[schedule_write] reconcile store={store} dates={dates[0]}..{dates[-1]} newly_published={len(keys)}")
    return len(keys)


_NOT_SCHEDULE = ("square", "adp_shifts", "adp_punches", "adp_rates", "adp_liability",
                 "adp_timecard_gaps", "square_rollup")


def refresh_schedule(page, store: str, week: dt.date) -> bool:
    """Re-scrape ``week`` from the grid already open on it and reload adp_scheduled_shifts.

    The console reads ADP's schedule from BQ; without this, a save/delete/publish
    only shows after the next sync. The loader purges each scraped date first, so
    removed shifts disappear too. Best-effort: the ADP write already happened.
    """
    from skills.adp_run_automation import runner as r

    try:
        frame = r._open_team_schedule(page)
        label = frame.get_by_text(re.compile(r"Week of")).first.inner_text(timeout=5000)
        if wb.week_label_start(label) != week:
            raise wb.ScheduleWriteError(f"grid shows {label!r}, not {week}")
        payloads, requests = r._schedule_within_session(page, weeks=1)
        path = r._write_schedule_json(payloads, store=store, requests=requests)
        subprocess.run(
            [sys.executable, "-m", "agents.bhaga.scripts.backfill_from_downloads", "--store", store,
             *[a for s in _NOT_SCHEDULE for a in ("--skip", s)]],
            check=True, env={**os.environ, "BHAGA_DATASTORE": "bigquery", "PYTHONUNBUFFERED": "1"},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"BREADCRUMB adp_schedule_refresh store={store} week_start={week} error={type(exc).__name__}: {exc}"[:500])
        return False
    print(f"[schedule_write] schedule refreshed week_start={week} from {path.name}")
    return True


def run_drafts(store: str, push_id: str, *, headless: bool, dry_run: bool) -> int:
    rows = _query(
        "SELECT * FROM {T} WHERE store = @store AND push_id = @push AND status = 'queued'"
        " ORDER BY date, start_min",
        [("store", "STRING", store), ("push", "STRING", push_id)],
    )
    done = {
        r["row_key"]
        for r in _query(
            "SELECT DISTINCT row_key FROM {T} WHERE store = @store AND push_id != @push"
            " AND status IN ('drafted', 'published')",
            [("store", "STRING", store), ("push", "STRING", push_id)],
        )
    }
    todo = []
    for r in rows:
        if r["row_key"] in done:
            print(f"[schedule_write] skip already-drafted {r['row_key']}")
            if not dry_run:
                _set_status(r["row_key"], push_id, "skipped", "already drafted in ADP by an earlier save")
        else:
            todo.append(r)
    print(f"[schedule_write] push_id={push_id} queued={len(rows)} to_create={len(todo)} dry_run={dry_run}")
    if not todo:
        return 0

    from skills.adp_run_automation import runner as r

    by_week: dict[dt.date, list[dict]] = defaultdict(list)
    for row in todo:
        by_week[week_start_of(row["date"])].append(row)
    failed = 0
    changed: set[dt.date] = set()
    pending = {row["row_key"] for row in todo}
    # BQ DML takes seconds; one ordered background writer keeps it off the browser's path.
    writer = ThreadPoolExecutor(max_workers=1)

    def set_status(row_key: str, status: str, error: str | None = None) -> None:
        if not dry_run:
            writer.submit(_set_status, row_key, push_id, status, error)

    def fail(row_key: str, msg: str) -> None:
        print(f"BREADCRUMB adp_schedule_write push_id={push_id} row_key={row_key} error={msg}")
        set_status(row_key, "failed", msg)

    def recheck(page, week: dt.date, row: dict, expected: int):
        """After a lagging count: reload the grid and ask ADP whether the shift exists."""
        frame = r._open_team_schedule(page)
        wb.goto_week(frame, page, week)
        page.wait_for_timeout(1500)
        n = wb.drafts_pending(frame)
        print(f"[schedule_write] recheck row_key={row['row_key']} publish_drafts={n} expected={expected + 1}")
        return frame, n

    try:
        with r.adp_session(store=store, headed=not headless) as (_ctx, page):
            frame = r._open_team_schedule(page)
            for week in sorted(by_week):
                wb.goto_week(frame, page, week)
                expected = wb.drafts_pending(frame)
                for row in by_week[week]:
                    pending.discard(row["row_key"])
                    iso = row["date"].isoformat()
                    adp_name = "open shift"
                    started = time.monotonic()
                    try:
                        if row["employee"]:
                            adp_name = wb.create_shift(
                                frame, page, date_iso=iso, employee=row["employee"],
                                start_min=row["start_min"], end_min=row["end_min"],
                                before=expected, dry_run=dry_run,
                            )
                        else:
                            wb.create_open_shift(
                                frame, page, date_iso=iso, start_min=row["start_min"],
                                end_min=row["end_min"], before=expected, dry_run=dry_run,
                            )
                    except wb.UnconfirmedSave as exc:
                        frame, n = recheck(page, week, row, expected)
                        if n == expected:
                            # ADP has no new draft, so nothing was saved; safe to move on.
                            failed += 1
                            fail(row["row_key"], f"not saved — ADP still shows {n} drafts")
                            continue
                        if n != expected + 1:
                            fail(row["row_key"], f"UnconfirmedSave: {exc} (ADP shows {n}, expected {expected + 1})"[:400])
                            for key in sorted(pending):
                                fail(key, "not attempted: stopped after an unconfirmed save")
                            return 1
                    except Exception as exc:  # noqa: BLE001 — one bad shift must not stop the rest
                        failed += 1
                        fail(row["row_key"], f"{type(exc).__name__}: {exc}"[:400])
                        continue
                    if not dry_run:
                        expected += 1
                        changed.add(week)
                    print(
                        f"[schedule_write] {'checked' if dry_run else 'drafted'} {row['row_key']} "
                        f"as {adp_name!r} in {time.monotonic() - started:.1f}s"
                    )
                    set_status(row["row_key"], "drafted")
            if changed:
                # The grid is on the last week written; the console saves one week per click.
                refresh_schedule(page, store, max(changed))
    finally:
        writer.shutdown(wait=True)
    return 1 if failed else 0


def _clock(m: int) -> str:
    h, mm = divmod(m, 60)
    return f"{(h - 1) % 12 + 1}:{mm:02d} {'AM' if h < 12 else 'PM'}"


def _week_label(week_start: dt.date) -> str:
    end = week_start + dt.timedelta(days=6)
    tail = f"{end.day}" if end.month == week_start.month else f"{end:%b} {end.day}"
    return f"{week_start:%b} {week_start.day}–{tail}"


def publish_message(week_start: dt.date, open_shifts: list[dict]) -> str:
    """ClickUp DM to the operator: a ready-to-post note for the team.

    ``open_shifts``: ``{"date": date, "start_min": int, "end_min": int}`` rows.
    """
    week = _week_label(week_start)
    lines = [
        f"Published **{week}** in ADP. Here's a draft for the team — copy and post:",
        "",
        "---",
        "",
        f"Hi team! The schedule for **{week}** is now published in ADP. Thank you for all the hard work "
        "and flexibility — it really shows.",
        "",
        "Please take a moment to look over your shifts and make sure they work with your availability. "
        "If anything doesn't look right, just let me know and we'll sort it out together.",
        "",
    ]
    if open_shifts:
        lines += ["**Open shifts this week**", ""]
        for o in sorted(open_shifts, key=lambda x: (x["date"], x["start_min"])):
            d = o["date"]
            lines.append(f"- {d:%a %b} {d.day} · {_clock(o['start_min'])} – {_clock(o['end_min'])}")
        lines += [
            "",
            "If you'd like to pick one up, please claim it in the ADP app — that's the quickest way to "
            "lock it in. If you run into any trouble claiming it, reply in this thread and I'll add you.",
            "",
        ]
    else:
        lines += ["There are no open shifts this week.", ""]
    lines.append("Thank you, team!")
    return "\n".join(lines)


def notify_published(store: str, week_start: dt.date) -> None:
    """DM the operator a draft team message after a console publish. Best-effort, posts once."""
    from core.datastore import fq

    from agents.bhaga.scripts.team_pulse import DEFAULT_DM_USER_ID, DEFAULT_WORKSPACE_ID
    from skills.adp_run_automation.schedule_backend import _SHIFT_RANGE_RE, _to_minutes
    from skills.clickup_chat import ensure_dm_channel, post_message

    try:
        rows = _query(
            f"SELECT date, shift_range FROM {fq('adp_open_shifts')}"
            " WHERE week_start = @week ORDER BY date, slot_index",
            [("week", "DATE", week_start)],
        )
        open_shifts = []
        for r in rows:
            m = _SHIFT_RANGE_RE.search(r["shift_range"] or "")
            if m:
                open_shifts.append({
                    "date": r["date"],
                    "start_min": _to_minutes(int(m.group(1)), int(m.group(2)), m.group(3)),
                    "end_min": _to_minutes(int(m.group(4)), int(m.group(5)), m.group(6)),
                })
        channel = ensure_dm_channel([DEFAULT_DM_USER_ID], team_id=DEFAULT_WORKSPACE_ID)
        post_message(str(channel["id"]), publish_message(week_start, open_shifts), team_id=DEFAULT_WORKSPACE_ID)
    except Exception as exc:  # noqa: BLE001
        print(f"BREADCRUMB adp_publish_dm store={store} week_start={week_start} error={type(exc).__name__}: {exc}"[:500])
        return
    print(f"[schedule_write] publish DM sent week_start={week_start} open_shifts={len(open_shifts)}")


def run_publish(store: str, week_start: dt.date, *, headless: bool, dry_run: bool) -> int:
    from skills.adp_run_automation import runner as r

    with r.adp_session(store=store, headed=not headless) as (_ctx, page):
        frame = r._open_team_schedule(page)
        page.wait_for_timeout(2500)
        wb.goto_week(frame, page, week_start)
        try:
            pending = wb.publish_drafts(frame, page, dry_run=dry_run)
            if pending and not dry_run:
                refresh_schedule(page, store, week_start)
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"[:400]
            print(f"BREADCRUMB adp_schedule_publish store={store} week_start={week_start} error={msg}")
            if not dry_run:
                _query(
                    "UPDATE {T} SET error = @error, updated_at = CURRENT_TIMESTAMP()"
                    " WHERE store = @store AND week_start = @week AND status = 'drafted'",
                    [("error", "STRING", f"publish failed: {msg}"), ("store", "STRING", store),
                     ("week", "DATE", week_start)],
                )
            return 1
    print(f"[schedule_write] publish week_start={week_start} pending_drafts={pending} dry_run={dry_run}")
    if dry_run:
        return 0
    _query(
        "UPDATE {T} SET status = 'published', error = NULL, updated_at = CURRENT_TIMESTAMP()"
        " WHERE store = @store AND week_start = @week AND status = 'drafted'",
        [("store", "STRING", store), ("week", "DATE", week_start)],
    )
    if pending:
        notify_published(store, week_start)
    return 0


def run_delete(store: str, week_start: dt.date, row_keys: list[str], *, headless: bool, dry_run: bool) -> int:
    """Remove superseded draft shifts from ADP and mark their rows 'deleted'.

    Only rows that are currently 'drafted' in ``week_start`` and assigned to an
    employee are touched. Each delete must drop ADP's draft count by exactly one;
    anything else stops the run.
    """
    latest = _query(
        "SELECT * FROM {T} WHERE store = @store AND week_start = @week"
        " QUALIFY ROW_NUMBER() OVER (PARTITION BY row_key ORDER BY updated_at DESC) = 1",
        [("store", "STRING", store), ("week", "DATE", week_start)],
    )
    by_key = {r["row_key"]: r for r in latest}
    todo = []
    for key in row_keys:
        row = by_key.get(key)
        if row is None or row["status"] != "drafted" or not row["employee"]:
            print(f"[schedule_write] delete refused {key}: {row and row['status']!r} (needs an assigned 'drafted' row)")
            return 2
        todo.append(row)
    print(f"[schedule_write] delete week_start={week_start} rows={len(todo)} dry_run={dry_run}")
    if not todo:
        return 0

    from skills.adp_run_automation import runner as r

    with r.adp_session(store=store, headed=not headless) as (_ctx, page):
        frame = r._open_team_schedule(page)
        wb.goto_week(frame, page, week_start)
        page.wait_for_timeout(1500)
        expected = wb.drafts_pending(frame)
        rc, deleted = 0, 0
        for row in todo:
            started = time.monotonic()
            try:
                try:
                    wb.delete_shift(
                        frame, page, date_iso=row["date"].isoformat(), employee=row["employee"],
                        start_min=row["start_min"], end_min=row["end_min"], before=expected, dry_run=dry_run,
                    )
                except wb.UnconfirmedSave:
                    if dry_run:
                        raise
                    frame = r._open_team_schedule(page)
                    wb.goto_week(frame, page, week_start)
                    page.wait_for_timeout(1500)
                    n = wb.drafts_pending(frame)
                    print(f"[schedule_write] delete recheck row_key={row['row_key']} publish_drafts={n} expected={expected - 1}")
                    if n != expected - 1:
                        raise
            except Exception as exc:  # noqa: BLE001 — stop: the next delete's baseline would be unknown
                msg = f"{type(exc).__name__}: {exc}"[:400]
                print(f"BREADCRUMB adp_schedule_delete row_key={row['row_key']} expected={expected} error={msg}")
                rc = 1
                break
            print(
                f"[schedule_write] {'checked' if dry_run else 'deleted'} {row['row_key']}"
                f" in {time.monotonic() - started:.1f}s"
            )
            if not dry_run:
                expected -= 1
                deleted += 1
                _set_status(row["row_key"], row["push_id"], "deleted")
        if deleted:
            refresh_schedule(page, store, week_start)
    return rc


def run_refresh(store: str, week_start: dt.date, *, headless: bool) -> int:
    """Read-only: re-scrape one week of ADP Team Schedule into adp_scheduled_shifts."""
    from skills.adp_run_automation import runner as r

    with r.adp_session(store=store, headed=not headless) as (_ctx, page):
        frame = r._open_team_schedule(page)
        wb.goto_week(frame, page, week_start)
        page.wait_for_timeout(1500)
        return 0 if refresh_schedule(page, store, week_start) else 1


def run_inspect(store: str, week_start: dt.date, *, headless: bool) -> int:
    """Read-only: the week's "Publish drafts (N)" count and its open shifts, as ADP shows them."""
    from skills.adp_run_automation import runner as r

    with r.adp_session(store=store, headed=not headless) as (_ctx, page):
        frame = r._open_team_schedule(page)
        page.wait_for_timeout(2500)
        wb.goto_week(frame, page, week_start)
        page.wait_for_timeout(2000)
        try:
            pending: int | str = wb.drafts_pending(frame)
        except wb.ScheduleWriteError as exc:
            pending = f"unreadable ({exc})"
        print(f"[schedule_write] inspect week_start={week_start} publish_drafts={pending}")
        info = r._scrape_open_shifts(page, frame, week_label=str(week_start))
        for cell in info.get("open_shift_cells") or []:
            print(f"[schedule_write] inspect open {cell.get('heading')!r}: {cell.get('shifts')}")
        if info.get("open_shifts_error"):
            print(f"[schedule_write] inspect open_shifts_error={info['open_shifts_error']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--store", default="palmetto")
    p.add_argument("--push-id")
    p.add_argument("--publish", action="store_true")
    p.add_argument("--inspect", action="store_true", help="read-only: draft count + open shifts for --week-start")
    p.add_argument("--refresh-schedule", action="store_true", help="read-only: reload --week-start's ADP schedule into BQ")
    p.add_argument("--delete", help="';'-separated row_keys of drafted shifts to remove from ADP (--week-start)")
    p.add_argument("--week-start", type=dt.date.fromisoformat)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--headless", action="store_true")
    a = p.parse_args(argv)
    if a.refresh_schedule:
        if not a.week_start or a.week_start.weekday() != 0:
            p.error("--refresh-schedule needs --week-start on a Monday")
        return run_refresh(a.store, a.week_start, headless=a.headless)
    if a.delete:
        if not a.week_start or a.week_start.weekday() != 0:
            p.error("--delete needs --week-start on a Monday")
        keys = [k.strip() for k in a.delete.split(";") if k.strip()]
        return run_delete(a.store, a.week_start, keys, headless=a.headless, dry_run=a.dry_run)
    if a.inspect:
        if not a.week_start or a.week_start.weekday() != 0:
            p.error("--inspect needs --week-start on a Monday")
        return run_inspect(a.store, a.week_start, headless=a.headless)
    if a.publish:
        if not a.week_start or a.week_start.weekday() != 0:
            p.error("--publish needs --week-start on a Monday")
        return run_publish(a.store, a.week_start, headless=a.headless, dry_run=a.dry_run)
    if not a.push_id:
        p.error("--push-id is required (or --publish --week-start)")
    return run_drafts(a.store, a.push_id, headless=a.headless, dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
