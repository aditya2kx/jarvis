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
import sys
from collections import defaultdict

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
    pending = {row["row_key"] for row in todo}

    def fail(row_key: str, msg: str) -> None:
        print(f"BREADCRUMB adp_schedule_write push_id={push_id} row_key={row_key} error={msg}")
        if not dry_run:
            _set_status(row_key, push_id, "failed", msg)

    with r.adp_session(store=store, headed=not headless) as (_ctx, page):
        frame = r._open_team_schedule(page)
        page.wait_for_timeout(2500)
        for week in sorted(by_week):
            wb.goto_week(frame, page, week)
            for row in by_week[week]:
                pending.discard(row["row_key"])
                iso = row["date"].isoformat()
                adp_name = "open shift"
                try:
                    if row["employee"]:
                        adp_name = wb.create_shift(frame, page, date_iso=iso, employee=row["employee"],
                                                   start_min=row["start_min"], end_min=row["end_min"],
                                                   dry_run=dry_run)
                    else:
                        wb.create_open_shift(frame, page, date_iso=iso, start_min=row["start_min"],
                                             end_min=row["end_min"], dry_run=dry_run)
                except wb.UnconfirmedSave as exc:
                    fail(row["row_key"], f"UnconfirmedSave: {exc}"[:400])
                    for key in sorted(pending):
                        fail(key, "not attempted: stopped after an unconfirmed save")
                    return 1
                except Exception as exc:  # noqa: BLE001 — one bad shift must not stop the rest
                    failed += 1
                    fail(row["row_key"], f"{type(exc).__name__}: {exc}"[:400])
                    continue
                print(f"[schedule_write] {'checked' if dry_run else 'drafted'} {row['row_key']} as {adp_name!r}")
                if not dry_run:
                    _set_status(row["row_key"], push_id, "drafted")
    return 1 if failed else 0


def run_publish(store: str, week_start: dt.date, *, headless: bool, dry_run: bool) -> int:
    from skills.adp_run_automation import runner as r

    with r.adp_session(store=store, headed=not headless) as (_ctx, page):
        frame = r._open_team_schedule(page)
        page.wait_for_timeout(2500)
        wb.goto_week(frame, page, week_start)
        try:
            pending = wb.publish_drafts(frame, page, dry_run=dry_run)
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
    return 0


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
    p.add_argument("--week-start", type=dt.date.fromisoformat)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--headless", action="store_true")
    a = p.parse_args(argv)
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
