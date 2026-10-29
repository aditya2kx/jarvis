"""Push console draft shifts into ADP Team Schedule — Issue #337.

Two operator-confirmed modes, each started by one console click:

  drafts   python3 -m agents.bhaga.scripts.adp_schedule_write --store palmetto --push-id <id>
           Creates every ``labor_schedule_pushes`` row of that push (status='queued')
           as an ADP *draft* shift (assigned) or draft open shift (employee NULL).
           Rows whose row_key is already drafted/published are marked 'skipped'.
  publish  ... --publish --week-start 2026-09-28
           Clicks ADP "Publish drafts" for that week and flips the week's 'drafted'
           rows to 'published', re-reads ADP from tomorrow through that week, then
           DMs the operator a Shift Coverage draft listing every open shift left.
  dm       ... --publish-dm --week-start 2026-10-19 [--to channel]
           Re-reads ADP's open shifts, then re-sends the DM. ``--to channel`` posts the
           team note (from ``@everyone``, no draft header) to the store's Shift Coverage channel.
  assign   ... --assign-id <id>
           Assigns each queued ``labor_open_shift_assignments`` row's existing ADP open
           shift to its person (Issue #412), then re-reads the changed weeks.
  queue    ... --queue-assign shifts.json [--execute]
           Inserts those open shifts as ``queued`` rows (same checks as the console)
           and refuses while any ``BHAGA_ADP_SCHEDULE_WRITE`` execution is still
           running. ``--execute`` then starts the prod job with per-execution env
           overrides — it does not change the job's own env.

``--dry-run`` walks each ADP wizard to its final step and backs out; BQ is
left untouched. Cloud Run: daily_refresh early-exits here when
``BHAGA_ADP_SCHEDULE_WRITE`` is set (see that module).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from skills.adp_run_automation import schedule_write_backend as wb


ASSIGN_TABLE = "labor_open_shift_assignments"


def _bq(table: str = "labor_schedule_pushes"):
    from google.cloud import bigquery

    from core.datastore import fq, get_client

    client = get_client()
    if client is None:
        raise RuntimeError("BigQuery datastore is not enabled (BHAGA_DATASTORE=bigquery)")
    return client, fq(table), bigquery


def _query(sql: str, params: list[tuple[str, str, object]], *, table: str = "labor_schedule_pushes") -> list[dict]:
    client, table, bigquery = _bq(table)
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


def _known_open_slots(store: str, week: dt.date) -> set[tuple[str, int, int]]:
    """Open shifts BQ already records as saved in ADP for that week (date_iso, start, end)."""
    rows = _query(
        "SELECT DISTINCT date, start_min, end_min FROM {T} WHERE store = @store AND employee IS NULL"
        " AND status IN ('drafted', 'published') AND date BETWEEN @a AND @b",
        [("store", "STRING", store), ("a", "DATE", week), ("b", "DATE", week + dt.timedelta(days=6))],
    )
    return {(r["date"].isoformat(), r["start_min"], r["end_min"]) for r in rows}


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


def found_failed_rows(rows: list[dict], states: list[dict]) -> dict[str, str]:
    """Failed rows whose exact shift ADP shows → the status ADP implies ('drafted' / 'published').

    A save ADP made but the job could not confirm, or a shift added in ADP by hand.
    Exact match only: same day and times, and the same person (or open for an open row).
    """
    out: dict[str, str] = {}
    for r in rows:
        for s in states:
            if (
                s["date"] == str(r["date"]) and s["start_min"] == r["start_min"] and s["end_min"] == r["end_min"]
                and ((r["employee"] is None and s["employee"] is None)
                     or (r["employee"] and s["employee"] and _same_person(s["employee"], r["employee"])))
            ):
                out[r["row_key"]] = "drafted" if s["draft"] else "published"
                break
    return out


def reconcile_published(store: str, states: list[dict]) -> int:
    """Bring console rows in line with ADP's schedule on every schedule load.

    Drafted rows ADP shows published → 'published' (e.g. published in ADP). Failed rows
    whose shift ADP shows → 'drafted' / 'published', so the console never reports a
    shift as failed that is in ADP (Issue #406).
    """
    dates = sorted({s["date"] for s in states})
    if not dates:
        return 0
    latest = _query(
        "SELECT push_id, row_key, status, CAST(date AS STRING) AS date, employee, start_min, end_min FROM {T}"
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
    failed = [r for r in latest if r["status"] == "failed"]
    found = found_failed_rows(failed, states)
    push_of = {r["row_key"]: r["push_id"] for r in failed}
    for status in ("drafted", "published"):
        ids = [f"{push_of[k]}|{k}" for k, v in found.items() if v == status]
        if ids:
            _query(
                "UPDATE {T} SET status = @status, error = NULL, updated_at = CURRENT_TIMESTAMP()"
                " WHERE store = @store AND status = 'failed'"
                " AND CONCAT(push_id, '|', row_key) IN UNNEST(SPLIT(@ids, '\\n'))",
                [("store", "STRING", store), ("status", "STRING", status), ("ids", "STRING", "\n".join(ids))],
            )
    for k, v in sorted(found.items()):
        print(f"[schedule_write] reconcile failed row {k} is in ADP → {v}")
    print(
        f"[schedule_write] reconcile store={store} dates={dates[0]}..{dates[-1]} "
        f"newly_published={len(keys)} failed_found_in_adp={len(found)}"
    )
    return len(keys)


_NOT_SCHEDULE = ("square", "adp_shifts", "adp_punches", "adp_rates", "adp_liability",
                 "adp_timecard_gaps", "square_rollup")


def refresh_schedule(page, store: str, week: dt.date, *, weeks: int = 1) -> bool:
    """Re-scrape ``weeks`` weeks from ``week`` (grid already open on it) and reload BQ.

    The console reads ADP's schedule from BQ; without this, a save/delete/publish
    only shows after the next sync. The loader purges each scraped date first, so
    removed shifts disappear too. Best-effort: the ADP write already happened.
    """
    from skills.adp_run_automation import runner as r

    try:
        frame = r._open_team_schedule(page)
        wb._back_out(frame, page)
        label = frame.get_by_text(re.compile(r"Week of")).first.inner_text(timeout=5000)
        if wb.week_label_start(label) != week:
            raise wb.ScheduleWriteError(f"grid shows {label!r}, not {week}")
        payloads, requests = r._schedule_within_session(page, weeks=weeks)
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


def refresh_open_shift_window(page, store: str, week_start: dt.date) -> bool:
    """Re-read every week the publish post lists open shifts for, so claimed shifts drop off."""
    from skills.adp_run_automation import runner as r

    first = min(week_start_of(open_shift_window(week_start, _today_ct())[0]), week_start)
    if first != week_start:
        try:
            wb.goto_week(r._open_team_schedule(page), page, first)
        except Exception as exc:  # noqa: BLE001
            print(f"BREADCRUMB adp_schedule_refresh store={store} week_start={first} error={type(exc).__name__}: {exc}"[:500])
            return False
    return refresh_schedule(page, store, first, weeks=(week_start - first).days // 7 + 1)


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
    stopped = False
    changed: set[dt.date] = set()
    pending = {row["row_key"] for row in todo}
    settled: set[str] = set()
    # BQ DML takes seconds; one ordered background writer keeps it off the browser's path.
    writer = ThreadPoolExecutor(max_workers=1)

    def set_status(row_key: str, status: str, error: str | None = None) -> None:
        settled.add(row_key)
        if not dry_run:
            writer.submit(_set_status, row_key, push_id, status, error)

    def fail(row_key: str, msg: str) -> None:
        print(f"BREADCRUMB adp_schedule_write push_id={push_id} row_key={row_key} error={msg}")
        set_status(row_key, "failed", msg)

    def slot(row: dict) -> tuple[str, int, int]:
        return (row["date"].isoformat(), row["start_min"], row["end_min"])

    def in_adp(frame, page, row: dict, open_before: Counter) -> bool:
        """Read-only: whether ADP now has this row's shift (open: one more than before)."""
        if row["employee"]:
            return wb.has_draft_shift(
                frame, page, date_iso=row["date"].isoformat(), employee=row["employee"],
                start_min=row["start_min"], end_min=row["end_min"],
            )
        return wb.read_open_shifts(page, frame, week_start_of(row["date"]))[slot(row)] > open_before[slot(row)]

    def reopen(page, week: dt.date):
        frame = r._open_team_schedule(page)
        wb._back_out(frame, page)
        wb.goto_week(frame, page, week)
        page.wait_for_timeout(1500)
        return frame

    def count_now(frame, fallback: int) -> int:
        try:
            return wb.drafts_pending(frame)
        except wb.ScheduleWriteError:
            return fallback

    try:
        with r.adp_session(store=store, headed=not headless) as (_ctx, page):
            frame = r._open_team_schedule(page)
            try:
                for week in sorted(by_week):
                    wb.goto_week(frame, page, week)
                    expected = wb.drafts_pending(frame)
                    open_adp: Counter = Counter()
                    unclaimed: Counter = Counter()
                    if any(not row["employee"] for row in by_week[week]):
                        # A save ADP made but never confirmed is in ADP and not in BQ;
                        # adopt it rather than create a duplicate.
                        open_adp = wb.read_open_shifts(page, frame, week)
                        known = _known_open_slots(store, week)
                        unclaimed = Counter({k: n - (k in known) for k, n in open_adp.items()})
                        print(f"[schedule_write] week={week} adp_open={dict(open_adp)} known={sorted(known)}")
                    for row in by_week[week]:
                        pending.discard(row["row_key"])
                        iso = row["date"].isoformat()
                        adp_name = "open shift"
                        started = time.monotonic()
                        if not row["employee"] and unclaimed[slot(row)] > 0:
                            unclaimed[slot(row)] -= 1
                            print(f"[schedule_write] adopted {row['row_key']} — already in ADP")
                            set_status(row["row_key"], "drafted")
                            continue
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
                            # Ask ADP for this exact shift: a draft count can't tell a lag from a miss.
                            try:
                                frame = reopen(page, week)
                                present = in_adp(frame, page, row, open_adp)
                            except Exception as rexc:  # noqa: BLE001 — unknown = unconfirmed
                                # The console holds this shift until ADP's schedule is re-read.
                                fail(row["row_key"], f"UnconfirmedSave: {exc} (recheck failed: {type(rexc).__name__}: {rexc})"[:400])
                                for key in sorted(pending):
                                    fail(key, "not attempted: stopped after an unconfirmed save")
                                changed.add(week)
                                stopped = True
                                break
                            print(f"[schedule_write] recheck row_key={row['row_key']} in_adp={present}")
                            if not present:
                                failed += 1
                                fail(row["row_key"], "not saved — ADP does not show this shift")
                                expected = count_now(frame, expected)
                                continue
                        except Exception as exc:  # noqa: BLE001 — one bad shift must not stop the rest
                            try:
                                frame = reopen(page, week)
                                present = not dry_run and bool(row["employee"]) and in_adp(frame, page, row, open_adp)
                            except Exception:  # noqa: BLE001
                                present = False
                            if not present:
                                failed += 1
                                fail(row["row_key"], f"{type(exc).__name__}: {exc}"[:400])
                                expected = count_now(frame, expected)
                                continue
                            print(f"[schedule_write] adopted {row['row_key']} — already in ADP ({type(exc).__name__})")
                            expected = count_now(frame, expected)
                            set_status(row["row_key"], "drafted")
                            continue
                        if not dry_run:
                            expected = count_now(frame, expected + 1)
                            if not row["employee"]:
                                open_adp[slot(row)] += 1
                            changed.add(week)
                        print(
                            f"[schedule_write] {'checked' if dry_run else 'drafted'} {row['row_key']} "
                            f"as {adp_name!r} in {time.monotonic() - started:.1f}s"
                        )
                        set_status(row["row_key"], "drafted")
                    if stopped:
                        break
            finally:
                if changed and not dry_run:
                    # Statuses land first: the console trusts a schedule read newer than a row's updated_at.
                    writer.submit(lambda: None).result()
                    # The grid is on the last week written; the console saves one week per click.
                    refresh_schedule(page, store, max(changed))
    except Exception as exc:
        # A crash must not strand rows as 'queued' — the console reads that as still in flight.
        for row in todo:
            if row["row_key"] not in settled:
                fail(row["row_key"], f"not attempted: job stopped ({type(exc).__name__}: {exc})"[:400])
        raise
    finally:
        writer.shutdown(wait=True)
    return 1 if failed or stopped else 0


def run_assign(store: str, assign_id: str, *, headless: bool, dry_run: bool) -> int:
    """Assign each queued ``labor_open_shift_assignments`` row's ADP open shift to its person.

    Confirmed only when ADP's open-shift list for that slot drops by one. A slot ADP no
    longer lists open fails without a click — an assigned shift is never open, so a
    retry can't assign twice. Then re-reads the changed weeks into BQ.
    """
    def aq(sql: str, params: list[tuple[str, str, object]]) -> list[dict]:
        return _query(sql, params, table=ASSIGN_TABLE)

    rows = aq(
        "SELECT * FROM {T} WHERE store = @store AND assign_id = @id AND status = 'queued' ORDER BY date, start_min",
        [("store", "STRING", store), ("id", "STRING", assign_id)],
    )
    done = {
        r["row_key"]
        for r in aq(
            "SELECT DISTINCT row_key FROM {T} WHERE store = @store AND assign_id != @id AND status = 'assigned'",
            [("store", "STRING", store), ("id", "STRING", assign_id)],
        )
    }
    settled: set[str] = set()

    def set_status(row_key: str, status: str, error: str | None = None, adp_name: str | None = None) -> None:
        settled.add(row_key)
        if dry_run:
            return
        aq(
            "UPDATE {T} SET status = @status, error = @error, adp_name = @name, updated_at = CURRENT_TIMESTAMP()"
            " WHERE assign_id = @id AND row_key = @key",
            [("status", "STRING", status), ("error", "STRING", error), ("name", "STRING", adp_name),
             ("id", "STRING", assign_id), ("key", "STRING", row_key)],
        )

    failed = 0

    def fail(row_key: str, msg: str) -> None:
        nonlocal failed
        failed += 1
        print(f"BREADCRUMB adp_open_shift_assign assign_id={assign_id} row_key={row_key} error={msg}"[:500])
        set_status(row_key, "failed", msg[:400])

    todo = rows
    print(f"[schedule_write] assign_id={assign_id} queued={len(rows)} dry_run={dry_run}")
    if not todo:
        return 0

    from skills.adp_run_automation import runner as r

    by_week: dict[dt.date, list[dict]] = defaultdict(list)
    for row in todo:
        by_week[week_start_of(row["date"])].append(row)
    changed: set[dt.date] = set()

    def slot(row: dict) -> tuple[str, int, int]:
        return (row["date"].isoformat(), row["start_min"], row["end_min"])

    try:
        with r.adp_session(store=store, headed=not headless) as (_ctx, page):
            frame = r._open_team_schedule(page)
            try:
                for week in sorted(by_week):
                    wb.goto_week(frame, page, week)
                    page.wait_for_timeout(1500)
                    open_now = wb.read_open_shifts(page, frame, week)
                    for row in by_week[week]:
                        key = row["row_key"]
                        # An earlier 'assigned' row only counts while ADP agrees: one undone in ADP is open again.
                        if open_now[slot(row)] <= 0:
                            if key in done:
                                print(f"[schedule_write] skip already-assigned {key}")
                                set_status(key, "skipped", "already assigned by an earlier Write to ADP")
                            else:
                                fail(key, "no longer an open shift in ADP (claimed, assigned or removed)")
                            continue
                        try:
                            name = wb.assign_open_shift(
                                frame, page, date_iso=row["date"].isoformat(), employee=row["employee"],
                                start_min=row["start_min"], end_min=row["end_min"], dry_run=dry_run,
                            )
                        except Exception as exc:  # noqa: BLE001 — one shift must not stop the rest
                            fail(key, f"{type(exc).__name__}: {exc}")
                            continue
                        if dry_run:
                            print(f"[schedule_write] checked {key} as {name!r}")
                            settled.add(key)
                            continue
                        changed.add(week)
                        try:
                            after = wb.read_open_shifts(page, frame, week)
                        except Exception as exc:  # noqa: BLE001 — unknown; a retry can't double-assign
                            fail(key, f"UnconfirmedAssign: could not re-read ADP's open shifts ({type(exc).__name__}: {exc})")
                            continue
                        if after[slot(row)] < open_now[slot(row)]:
                            print(f"[schedule_write] assigned {key} as {name!r}")
                            set_status(key, "assigned", adp_name=name)
                        else:
                            fail(key, "ADP did not confirm the assignment — the open shift is still listed")
                        open_now = after
            finally:
                if changed and not dry_run:
                    lo, hi = min(changed), max(changed)
                    try:
                        wb.goto_week(r._open_team_schedule(page), page, lo)
                        refresh_schedule(page, store, lo, weeks=(hi - lo).days // 7 + 1)
                    except Exception as exc:  # noqa: BLE001 — the assignments already happened
                        print(f"BREADCRUMB adp_schedule_refresh store={store} week_start={lo} error={type(exc).__name__}: {exc}"[:500])
    except Exception as exc:
        for row in todo:
            if row["row_key"] not in settled:
                fail(row["row_key"], f"not attempted: job stopped ({type(exc).__name__}: {exc})")
        raise
    return 1 if failed else 0


def _clock(m: int) -> str:
    h, mm = divmod(m, 60)
    return f"{(h - 1) % 12 + 1}:{mm:02d} {'AM' if h < 12 else 'PM'}"


def _week_label(week_start: dt.date) -> str:
    end = week_start + dt.timedelta(days=6)
    tail = f"{end.day}" if end.month == week_start.month else f"{end:%b} {end.day}"
    return f"{week_start:%b} {week_start.day}–{tail}"


def _today_ct() -> dt.date:
    return dt.datetime.now(ZoneInfo("America/Chicago")).date()


def open_shift_window(week_start: dt.date, today: dt.date) -> tuple[dt.date, dt.date]:
    """Dates the publish post lists open shifts for: tomorrow through the published week's Sunday."""
    return today + dt.timedelta(days=1), week_start + dt.timedelta(days=6)


def team_post(week_start: dt.date, open_shifts: list[dict]) -> str:
    """The note the team sees, starting at ``@everyone``. No draft header."""
    week = _week_label(week_start)
    lines = [
        f"@everyone 🗞️ Hi team! The schedule for **{week}** is now published in ADP. "
        "Please take a moment to look over your shifts and make sure they work with your availability. "
        "If anything doesn't look right, just let me know in this thread and we'll sort it out together.",
    ]
    if open_shifts:
        lines += [
            "",
            "Additionally, sharing the Open Shifts for the coming days where we really need folks, please let "
            "us know if someone can pick these up. We are flexible around below times. 🙏🏽",
            "",
        ]
        by_day: dict[dt.date, list[dict]] = defaultdict(list)
        for o in open_shifts:
            by_day[o["date"]].append(o)
        for i, d in enumerate(sorted(by_day), 1):
            lines.append(f"{i}. {d:%b} {d.day} ({d:%A})")
            for j, o in enumerate(sorted(by_day[d], key=lambda x: (x["start_min"], x["end_min"])), 1):
                lines.append(f"    {j}. {_clock(o['start_min'])} → {_clock(o['end_min'])}")
    return "\n".join(lines)


def publish_message(week_start: dt.date, open_shifts: list[dict], *, stale_as_of: str | None = None) -> str:
    """ClickUp DM to the operator: a ready-to-post Shift Coverage note for the team.

    ``open_shifts``: ``{"date": date, "start_min": int, "end_min": int}`` rows, already
    limited to ``open_shift_window``. ``stale_as_of`` notes that ADP could not be
    re-read, so claimed shifts may still be listed. The part after the ``---`` is
    ``team_post`` — what gets posted to the channel.
    """
    week = _week_label(week_start)
    header = f"Published **{week}** in ADP. Here's a draft for Shift Coverage & Trades — copy and post:"
    if stale_as_of:
        header += f"\n\n_ADP re-read failed — open shifts are as of {stale_as_of}; check before posting._"
    return f"{header}\n\n---\n\n{team_post(week_start, open_shifts)}"


def shift_coverage_target(store: str) -> tuple[str, str]:
    """(ClickUp team id, Shift Coverage channel id) from the store profile."""
    path = Path(__file__).resolve().parents[3] / "agents/bhaga/knowledge-base/store-profiles" / f"{store}.json"
    clickup = json.loads(path.read_text())["clickup"]
    return str(clickup["team_id"]), str(clickup["shift_coverage_channel"]["id"])


def open_shifts_between(lo: dt.date, hi: dt.date) -> tuple[list[dict], str | None]:
    """ADP open shifts dated ``lo``..``hi`` from BQ, plus when they were last scraped (CT)."""
    from core.datastore import fq

    from skills.adp_run_automation.schedule_backend import _SHIFT_RANGE_RE, _to_minutes

    rows = _query(
        f"SELECT date, shift_range, scraped_at_utc FROM {fq('adp_open_shifts')}"
        " WHERE date BETWEEN @lo AND @hi ORDER BY date, slot_index",
        [("lo", "DATE", lo), ("hi", "DATE", hi)],
    )
    out = []
    for r in rows:
        m = _SHIFT_RANGE_RE.search(r["shift_range"] or "")
        if m:
            out.append({
                "date": r["date"],
                "start_min": _to_minutes(int(m.group(1)), int(m.group(2)), m.group(3)),
                "end_min": _to_minutes(int(m.group(4)), int(m.group(5)), m.group(6)),
            })
    scraped = max((r["scraped_at_utc"] for r in rows if r.get("scraped_at_utc")), default=None)
    if scraped is None:
        return out, None
    if isinstance(scraped, str):
        scraped = dt.datetime.fromisoformat(scraped.replace("Z", "+00:00"))
    if scraped.tzinfo is None:
        scraped = scraped.replace(tzinfo=dt.timezone.utc)
    when = scraped.astimezone(ZoneInfo("America/Chicago"))
    return out, f"{when:%b} {when.day} {_clock(when.hour * 60 + when.minute)} CT"


def notify_published(store: str, week_start: dt.date, *, fresh: bool = True, to: str = "dm") -> bool:
    """Send the schedule note. ``dm`` (the publish default) or ``channel`` (Shift Coverage).

    A channel post is the team note only, and only after a fresh ADP read — a stale
    list of open shifts is never posted to the team.
    """
    from agents.bhaga.scripts.team_pulse import DEFAULT_DM_USER_ID, DEFAULT_WORKSPACE_ID
    from agents.bhaga.scripts.unavailability_reminder import browser_send
    from skills.clickup_chat import ensure_dm_channel, post_message

    if to == "channel" and not fresh:
        print(
            f"BREADCRUMB adp_publish_channel store={store} week_start={week_start}"
            " error=stale ADP read; not posted to the team"
        )
        return False
    try:
        open_shifts, as_of = open_shifts_between(*open_shift_window(week_start, _today_ct()))
        if to == "channel":
            content = team_post(week_start, open_shifts)
            team_id, channel_id = shift_coverage_target(store)
        else:
            content = publish_message(week_start, open_shifts, stale_as_of=None if fresh else (as_of or "unknown"))
            team_id = DEFAULT_WORKSPACE_ID
            channel_id = str(ensure_dm_channel([DEFAULT_DM_USER_ID], team_id=team_id)["id"])
        # The API stores @everyone as plain text; the web app makes it a real mention.
        try:
            browser_send(store, channel_id, content, team_id)
        except Exception as exc:  # noqa: BLE001 — still deliver, as plain text
            print(f"BREADCRUMB adp_publish_browser store={store} week_start={week_start} to={to}"
                  f" error={type(exc).__name__}: {exc} — sent by the API instead"[:500])
            post_message(channel_id, content, team_id=team_id)
    except Exception as exc:  # noqa: BLE001
        print(f"BREADCRUMB adp_publish_dm store={store} week_start={week_start} to={to} error={type(exc).__name__}: {exc}"[:500])
        return False
    where = "Shift Coverage" if to == "channel" else "DM"
    print(f"[schedule_write] publish {where} sent week_start={week_start} open_shifts={len(open_shifts)} fresh={fresh}")
    return True


def _drafted_left(store: str, week_start: dt.date) -> int:
    """Rows of the week still 'drafted' (after the refresh's reconcile)."""
    rows = _query(
        "SELECT status FROM {T} WHERE store = @store AND week_start = @week"
        " QUALIFY ROW_NUMBER() OVER (PARTITION BY row_key ORDER BY updated_at DESC) = 1",
        [("store", "STRING", store), ("week", "DATE", week_start)],
    )
    return sum(r["status"] == "drafted" for r in rows)


def _reloaded_draft_count(page, week_start: dt.date, *, attempts: int = 3) -> int | None:
    """The week's "Publish drafts (N)" count on a freshly loaded grid; None if never readable.

    Right after Publish the open grid can keep the pre-publish toolbar count and the
    open-shift cells' "Drafts: N" for minutes, while a fresh load shows 0.
    """
    from skills.adp_run_automation import runner as r

    n = None
    for i in range(1, attempts + 1):
        page.wait_for_timeout(10_000)
        try:
            page.reload(wait_until="domcontentloaded")
            frame = r._open_team_schedule(page)
            page.wait_for_timeout(2500)
            wb.goto_week(frame, page, week_start)
            page.wait_for_timeout(2000)
            n = wb.drafts_pending(frame)
        except Exception as exc:  # noqa: BLE001
            print(f"[schedule_write] reload {i}/{attempts} week_start={week_start} unreadable: {type(exc).__name__}: {exc}"[:300])
            continue
        print(f"[schedule_write] reload {i}/{attempts} week_start={week_start} publish_drafts={n}")
        if n == 0:
            return 0
    return n


def run_publish(store: str, week_start: dt.date, *, headless: bool, dry_run: bool) -> int:
    from skills.adp_run_automation import runner as r

    # Rows a previous publish left 'drafted' although ADP published them still need marking + the DM.
    had_drafted = _drafted_left(store, week_start)
    with r.adp_session(store=store, headed=not headless) as (_ctx, page):
        frame = r._open_team_schedule(page)
        page.wait_for_timeout(2500)
        wb.goto_week(frame, page, week_start)
        fresh = False
        try:
            try:
                pending = wb.publish_drafts(frame, page, dry_run=dry_run)
            except wb.PublishUnconfirmed as exc:
                print(f"[schedule_write] {exc}; reloading the week to check")
                if _reloaded_draft_count(page, week_start) == 0:
                    print(f"[schedule_write] publish confirmed by reload week_start={week_start}")
                elif refresh_schedule(page, store, week_start) and not _drafted_left(store, week_start):
                    print(f"[schedule_write] publish confirmed by schedule re-read week_start={week_start}")
                else:
                    raise
                pending = exc.pending
            if (pending or had_drafted) and not dry_run:
                fresh = refresh_open_shift_window(page, store, week_start)
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
    print(
        f"[schedule_write] publish week_start={week_start} pending_drafts={pending}"
        f" bq_drafted={had_drafted} dry_run={dry_run}"
    )
    if dry_run:
        return 0
    _query(
        "UPDATE {T} SET status = 'published', error = NULL, updated_at = CURRENT_TIMESTAMP()"
        " WHERE store = @store AND week_start = @week AND status = 'drafted'",
        [("store", "STRING", store), ("week", "DATE", week_start)],
    )
    if pending or had_drafted:
        notify_published(store, week_start, fresh=fresh)
    return 0


def run_publish_dm(store: str, week_start: dt.date, *, headless: bool, to: str = "dm") -> int:
    """Console "Send ClickUp message": re-read ADP's open shifts, then DM or post to Shift Coverage."""
    from skills.adp_run_automation import runner as r

    fresh = False
    try:
        with r.adp_session(store=store, headed=not headless) as (_ctx, page):
            frame = r._open_team_schedule(page)
            page.wait_for_timeout(2500)
            wb.goto_week(frame, page, week_start)
            fresh = refresh_open_shift_window(page, store, week_start)
    except Exception as exc:  # noqa: BLE001 — still DM, flagged as not freshly read
        print(f"BREADCRUMB adp_publish_dm_reread store={store} week_start={week_start} error={type(exc).__name__}: {exc}"[:500])
    return 0 if notify_published(store, week_start, fresh=fresh, to=to) else 1


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


_ASSIGN_JOB = ("https://run.googleapis.com/v2/projects/jarvis-bhaga-prod"
               "/locations/us-central1/jobs/bhaga-daily-refresh")
_MAX_ASSIGN = 40


class ScheduleWriteBusy(RuntimeError):
    """A BHAGA_ADP_SCHEDULE_WRITE execution is still running."""


def validate_assign_items(items, today: dt.date) -> list[dict]:
    """Same rules as the console's validateAssignItems. Raises ValueError."""
    if not isinstance(items, list) or not items:
        raise ValueError("No open shifts selected.")
    if len(items) > _MAX_ASSIGN:
        raise ValueError(f"Too many open shifts ({len(items)}); max {_MAX_ASSIGN}.")
    seen: set[tuple[str, str]] = set()
    out = []
    for i, raw in enumerate(items, start=1):
        where = f"Shift {i}"
        if not isinstance(raw, dict):
            raise ValueError(f"{where}: expected an object.")
        date = raw.get("date")
        if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ValueError(f"{where}: bad date.")
        day = dt.date.fromisoformat(date)
        if day <= today:
            raise ValueError(f"{where}: {date} is today or in the past.")
        start = raw.get("start_min", raw.get("startMin"))
        end = raw.get("end_min", raw.get("endMin"))
        if (not all(isinstance(n, int) and not isinstance(n, bool) for n in (start, end))
                or start < 0 or end > 24 * 60 or end <= start):
            raise ValueError(f"{where}: bad start/end time.")
        employee = raw.get("employee")
        employee = employee.strip() if isinstance(employee, str) else ""
        if not employee:
            raise ValueError(f"{where}: choose who takes it.")
        if (date, employee) in seen:
            raise ValueError(f"{where}: {employee} already takes another selected shift on {date}.")
        seen.add((date, employee))
        out.append({"date": day, "employee": employee, "start_min": start, "end_min": end})
    return out


def execution_env(execution: dict) -> dict[str, str]:
    containers = ((execution.get("template") or {}).get("containers") or [])
    return {
        e["name"]: e.get("value") or ""
        for c in containers for e in (c.get("env") or []) if e.get("name")
    }


def running_schedule_write(executions: list[dict]) -> str | None:
    """Name of an unfinished execution whose env sets BHAGA_ADP_SCHEDULE_WRITE."""
    for execution in executions:
        if execution.get("completionTime"):
            continue
        if execution_env(execution).get("BHAGA_ADP_SCHEDULE_WRITE"):
            return execution.get("name") or "(unnamed execution)"
    return None


def _gcp_token() -> str:
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    if not creds.token:
        raise RuntimeError("no GCP access token")
    return creds.token


def fetch_job_executions() -> list[dict]:
    req = urllib.request.Request(
        f"{_ASSIGN_JOB}/executions?pageSize=10",
        headers={"Authorization": f"Bearer {_gcp_token()}"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode()).get("executions") or []


def _insert_queued_assign(store: str, items: list[dict], by: str) -> str:
    assign_id = str(uuid.uuid4())
    payload = json.dumps([
        {"row_key": "|".join([store, i["date"].isoformat(), str(i["start_min"]), str(i["end_min"]), i["employee"]]),
         "date": i["date"].isoformat(), "employee": i["employee"],
         "start_min": i["start_min"], "end_min": i["end_min"]}
        for i in items
    ])
    _query(
        "INSERT INTO {T}"
        " (store, assign_id, row_key, date, employee, start_min, end_min,"
        "  status, error, adp_name, requested_by, requested_at, updated_at)"
        " SELECT @store, @id, JSON_VALUE(s, '$.row_key'), DATE(JSON_VALUE(s, '$.date')),"
        "        JSON_VALUE(s, '$.employee'), CAST(JSON_VALUE(s, '$.start_min') AS INT64),"
        "        CAST(JSON_VALUE(s, '$.end_min') AS INT64), 'queued', NULL, NULL, @by,"
        "        CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()"
        " FROM UNNEST(JSON_QUERY_ARRAY(@items)) AS s",
        [("store", "STRING", store), ("id", "STRING", assign_id), ("by", "STRING", by),
         ("items", "STRING", payload)],
        table=ASSIGN_TABLE,
    )
    return assign_id


def queue_assign(store: str, items, *, by: str, today: dt.date | None = None,
                 executions: list[dict] | None = None) -> str:
    """Queue open-shift rows, refusing while a schedule-write execution is running."""
    valid = validate_assign_items(items, today or _today_ct())
    found = fetch_job_executions() if executions is None else executions
    hit = running_schedule_write(found)
    if hit:
        raise ScheduleWriteBusy(f"ADP is busy with another schedule write ({hit})")
    return _insert_queued_assign(store, valid, by)


def start_assign_job(store: str, assign_id: str) -> str:
    """Start one assign execution. Overrides apply to this run only, not the job."""
    body = {"overrides": {"containerOverrides": [{"env": [
        {"name": "BHAGA_ADP_SCHEDULE_WRITE", "value": "assign"},
        {"name": "BHAGA_IGNORE_HALT", "value": "1"},
        {"name": "BHAGA_STORE", "value": store},
        {"name": "BHAGA_SCHEDULE_ASSIGN_ID", "value": assign_id},
    ]}]}}
    req = urllib.request.Request(
        f"{_ASSIGN_JOB}:run", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {_gcp_token()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        data = json.loads(res.read().decode())
    return ((data.get("metadata") or {}).get("name")) or data.get("name") or ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--store", default="palmetto")
    p.add_argument("--push-id")
    p.add_argument("--publish", action="store_true")
    p.add_argument("--publish-dm", action="store_true", help="re-read ADP's open shifts, then send the schedule note for --week-start")
    p.add_argument("--to", choices=("dm", "channel"), default="dm", help="with --publish-dm: DM the operator, or post to Shift Coverage")
    p.add_argument("--inspect", action="store_true", help="read-only: draft count + open shifts for --week-start")
    p.add_argument("--refresh-schedule", action="store_true", help="read-only: reload --week-start's ADP schedule into BQ")
    p.add_argument("--delete", help="';'-separated row_keys of drafted shifts to remove from ADP (--week-start)")
    p.add_argument("--assign-id", help="assign the queued open shifts of this labor_open_shift_assignments run")
    p.add_argument("--queue-assign", metavar="FILE",
                   help="queue open shifts from this JSON file ('-' = stdin); refuses while a schedule write is running")
    p.add_argument("--execute", action="store_true",
                   help="with --queue-assign: start the prod assign job after the rows are queued")
    p.add_argument("--requested-by", default="agent:cli")
    p.add_argument("--week-start", type=dt.date.fromisoformat)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--headless", action="store_true")
    a = p.parse_args(argv)
    if a.queue_assign:
        if any([a.assign_id, a.publish, a.publish_dm, a.inspect, a.refresh_schedule, a.delete, a.push_id]):
            p.error("--queue-assign stands alone")
        text = sys.stdin.read() if a.queue_assign == "-" else Path(a.queue_assign).read_text()
        try:
            items = json.loads(text)
            assign_id = queue_assign(a.store, items, by=a.requested_by)
        except (ScheduleWriteBusy, ValueError, json.JSONDecodeError) as exc:
            print(f"refused: {exc}")
            return 1
        print(f"queued assign_id={assign_id} rows={len(items)}")
        if not a.execute:
            return 0
        hit = running_schedule_write(fetch_job_executions())
        if hit:
            print(f"refused to start: ADP is busy ({hit}); rows stay queued under {assign_id}")
            return 1
        print(f"started {start_assign_job(a.store, assign_id)}")
        return 0
    if a.assign_id:
        return run_assign(a.store, a.assign_id, headless=a.headless, dry_run=a.dry_run)
    if a.refresh_schedule:
        if not a.week_start or a.week_start.weekday() != 0:
            p.error("--refresh-schedule needs --week-start on a Monday")
        return run_refresh(a.store, a.week_start, headless=a.headless)
    if a.delete:
        if not a.week_start or a.week_start.weekday() != 0:
            p.error("--delete needs --week-start on a Monday")
        keys = [k.strip() for k in a.delete.split(";") if k.strip()]
        return run_delete(a.store, a.week_start, keys, headless=a.headless, dry_run=a.dry_run)
    if a.publish_dm:
        if not a.week_start or a.week_start.weekday() != 0:
            p.error("--publish-dm needs --week-start on a Monday")
        return run_publish_dm(a.store, a.week_start, headless=a.headless, to=a.to)
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
