"""Write operator-approved punch fixes into ADP (Issue #356): a clock-out for an
open entry, or a whole entry for a scheduled day with no punch.

Entry point for the console "Write to ADP" button: the console flips the chosen
``punch_gap_decisions`` rows to ``applying`` and starts daily_refresh with
``BHAGA_PUNCH_FIX_APPLY_ONLY=1`` + ``BHAGA_PUNCH_FIX_DECISION_IDS``. This module
writes each one in a single ADP session and records ``applied`` /
``already_resolved`` / ``failed``.

``applied`` means the punch is in the Timecard export, not just in the Timecards
UI: ADP's report lags a UI save (Issue #358 — a fix written 40 s before the
download was missing from it), so the same login re-downloads the export until
every written punch is there. One still missing is ``not_in_hours`` and flips to
``applied`` on whichever later load sees it (``reconcile_not_in_hours``).
daily_refresh then loads the export this job left on disk, so ``adp_punches``
and the gaps table catch up without a second login.

Refuses to run unless ``BHAGA_PUNCH_FIX_WRITEBACK=1`` (FEATURE_FLAGS.md). A row
that is not left ``applied`` or ``already_resolved`` ends ``failed`` with a
greppable ``[punch-fix] FAIL`` breadcrumb — the ADP write is never retried
automatically; only the read-only download repeats.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
from typing import Optional

from core.datastore import _param_config, fq, get_client

WRITEBACK_FLAG = "BHAGA_PUNCH_FIX_WRITEBACK"
EVIDENCE_DIR = pathlib.Path.home() / ".bhaga" / "evidence" / "punch-fix"
NOT_IN_HOURS = "not_in_hours"
RESYNC_ATTEMPTS = 3


def _resync_wait_s() -> int:
    return int(os.environ.get("BHAGA_PUNCH_FIX_RESYNC_WAIT_S", "90"))


def punch_in_export(row: dict, punches: list[dict]) -> bool:
    """True when the parsed Timecard export holds the punch this decision wrote:
    a new entry (in_time set) matches on in and out, a filled clock-out on out."""
    return any(
        p["date"] == str(row["date"]) and p["employee_id"] == row["employee_id"]
        and p["out_time"] == row["out_time"]
        and (not row.get("in_time") or p["in_time"] == row["in_time"])
        for p in punches
    )


# Same rule as punch_in_export, against the loaded table, on each person-day's
# latest decision. Mirrored by the console's punchFixesNotInHours (queries.ts) so
# "in hours" means one thing on every surface.
_MISSING_FROM_HOURS_SQL = """
  SELECT decision_id, status FROM (
    SELECT d.decision_id, d.status, d.date, d.employee_id, d.in_time, d.out_time
    FROM {decisions} d
    WHERE d.store = {store}
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY d.store, d.date, d.employee_id ORDER BY d.decided_at DESC) = 1
  ) d
  WHERE d.status IN ('applied', 'not_in_hours') AND d.out_time IS NOT NULL
    AND NOT EXISTS (
      SELECT 1 FROM {punches} p
      WHERE p.date = d.date AND p.employee_id = d.employee_id
        AND p.out_time = d.out_time
        AND (d.in_time IS NULL OR p.in_time = d.in_time)
    )
"""


def _missing_sql(store_expr: str) -> str:
    return _MISSING_FROM_HOURS_SQL.format(
        decisions=fq("punch_gap_decisions"), punches=fq("adp_punches"), store=store_expr)


def missing_from_hours(client, store: str) -> list[dict]:
    """Written decisions (applied / not_in_hours) whose punch is not in adp_punches."""
    return [dict(r) for r in client.query(
        _missing_sql("@store"), job_config=_param_config([("store", "STRING", store)])).result()]


def missing_from_hours_sql(store: str) -> str:
    """The same check as a parameterless query (status.py's read_query)."""
    if not re.fullmatch(r"[a-z0-9_]+", store):
        raise ValueError(f"bad store slug: {store!r}")
    return _missing_sql(f"'{store}'")


def reconcile_not_in_hours(client, store: str) -> int:
    """Flip not_in_hours → applied for every decision whose punch is now loaded."""
    still = {r["decision_id"] for r in missing_from_hours(client, store)}
    sql = f"""UPDATE {fq("punch_gap_decisions")}
              SET status = 'applied', error = NULL, applied_at = CURRENT_TIMESTAMP()
              WHERE store = @store AND status = 'not_in_hours'
                AND decision_id NOT IN UNNEST(@still)"""
    from google.cloud import bigquery

    job = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("store", "STRING", store),
        bigquery.ArrayQueryParameter("still", "STRING", sorted(still)),
    ]))
    job.result()
    return job.num_dml_affected_rows or 0


def _load(client, store: str, ids: list[str]) -> list[dict]:
    from google.cloud import bigquery

    sql = f"""
      SELECT d.decision_id, CAST(d.date AS STRING) AS date, d.employee_id, d.action,
             d.in_time, d.out_time, d.open_entry_index, d.decided_by,
             g.raw_employee_name, g.entries_json, g.kind, g.rule
      FROM {fq("punch_gap_decisions")} d
      JOIN {fq("adp_timecard_gaps")} g
        ON g.store = d.store AND g.date = d.date AND g.employee_id = d.employee_id
      WHERE d.store = @store AND d.decision_id IN UNNEST(@ids) AND d.status = 'applying'
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("store", "STRING", store),
        bigquery.ArrayQueryParameter("ids", "STRING", ids),
    ])
    return [dict(r) for r in client.query(sql, job_config=cfg).result()]


def _set_status(client, decision_id: str, status: str, error: Optional[str]) -> None:
    client.query(
        f"""UPDATE {fq("punch_gap_decisions")}
            SET status = @status, error = @error,
                applied_at = IF(@status = 'applied', CURRENT_TIMESTAMP(), applied_at)
            WHERE decision_id = @id""",
        job_config=_param_config([
            ("status", "STRING", status), ("error", "STRING", error), ("id", "STRING", decision_id),
        ]),
    ).result()


def _fail_leftovers(client, store: str, ids: list[str], error: str) -> None:
    from google.cloud import bigquery

    client.query(
        f"""UPDATE {fq("punch_gap_decisions")} SET status = 'failed', error = @error
            WHERE store = @store AND decision_id IN UNNEST(@ids) AND status = 'applying'""",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("error", "STRING", error[:300]),
            bigquery.ScalarQueryParameter("store", "STRING", store),
            bigquery.ArrayQueryParameter("ids", "STRING", ids),
        ]),
    ).result()


def _expect_in(row: dict) -> Optional[str]:
    entries = json.loads(row["entries_json"] or "[]")
    idx = row["open_entry_index"]
    if idx is None or idx >= len(entries):
        return None
    return entries[idx].get("in")


def _resync_in_session(page, dashboard_url: str, store: str, applied: list[dict],
                       target_date: datetime.date) -> list[dict]:
    """Download the Timecard export in this login until every applied punch is in
    it; return the rows still missing. Leaves the export (+ target meta) and the
    Timecards UI JSON on disk for daily_refresh's load."""
    from skills.adp_run_automation import runner, shift_backend
    from skills.store_profile import load_aliases

    aliases = load_aliases(store)
    missing = list(applied)
    for attempt in range(1, RESYNC_ATTEMPTS + 1):
        page.wait_for_timeout(_resync_wait_s() * 1000)
        page.goto(dashboard_url, wait_until="domcontentloaded", timeout=60_000)
        path = runner._timecard_within_session(page, target_date=target_date, store=store)
        runner._write_target_meta(path, target_date)
        punches = shift_backend.parse_xlsx(path, employee_aliases=aliases)
        missing = [r for r in missing if not punch_in_export(r, punches)]
        print(f"[punch-fix] resync attempt={attempt}/{RESYNC_ATTEMPTS} missing={len(missing)}")
        if not missing:
            break
    page.goto(dashboard_url, wait_until="domcontentloaded", timeout=60_000)
    runner._write_timecards_ui_json(runner._timecard_gaps_within_session(page), store=store)
    return missing


def apply_decisions(store: str, ids: list[str]) -> dict:
    """Write each ``applying`` decision, then confirm each applied punch reached
    the Timecard export; return status counts plus ``max_date`` (latest date ADP
    now holds a fix for — its pay period is the one to reload) and
    ``export_on_disk`` (this login left a post-write export for that date)."""
    if os.environ.get(WRITEBACK_FLAG) != "1":
        raise RuntimeError(f"{WRITEBACK_FLAG}=1 is required to write to ADP")
    from skills.adp_run_automation import runner
    from skills.adp_run_automation import timecard_fix_backend as tfb
    from skills.adp_run_automation import timecard_ui_backend as tub

    client = get_client()
    rows = _load(client, store, ids)
    counts: dict = {tfb.APPLIED: 0, tfb.ALREADY_RESOLVED: 0, tfb.FAILED: 0, NOT_IN_HOURS: 0}
    print(f"[punch-fix] store={store} requested={len(ids)} applying={len(rows)}")
    if not rows:
        return counts
    applied: list[dict] = []

    def record(row: dict, status: str, error: Optional[str], evidence: Optional[str] = None):
        counts[status] += 1
        if status == tfb.APPLIED:
            applied.append(row)
        if status in (tfb.APPLIED, tfb.ALREADY_RESOLVED):
            d = datetime.date.fromisoformat(row["date"])
            counts["max_date"] = max(counts.get("max_date") or d, d)
        _set_status(client, row["decision_id"], status, error)
        line = (f"date={row['date']} emp={row['employee_id']!r} "
                f"{'in=' + str(row['in_time']) + ' ' if row.get('in_time') else ''}out={row['out_time']} "
                f"status={status}")
        if status == tfb.FAILED:
            if evidence and os.environ.get("BHAGA_SECRETS_BACKEND") == "gcp":
                try:
                    from agents.bhaga.scripts import gcs_cache
                    evidence = gcs_cache.upload_evidence(
                        pathlib.Path(evidence), refresh_date=datetime.date.fromisoformat(str(row["date"])))
                except Exception as exc:  # noqa: BLE001 — keep the local path in the breadcrumb
                    print(f"[punch-fix] evidence upload failed: {exc}")
            print(f"[punch-fix] FAIL {line} error={error!r} evidence={evidence}")
        else:
            print(f"[punch-fix] {line}")

    try:
        with runner.adp_session(store=store) as (_ctx, page):
            dashboard_url = page.url
            first = runner._open_timecards(page)
            current = tub.parse_month_view(first).employee_name
            ui = tfb.PlaywrightTimecardUi(page, evidence_dir=EVIDENCE_DIR)
            by_emp: dict[str, list[dict]] = {}
            for r in rows:
                by_emp.setdefault(r["raw_employee_name"] or r["employee_id"], []).append(r)
            for name, emp_rows in by_emp.items():
                try:
                    month = tub.parse_month_view(ui.select_employee(name, current))
                    current = name
                except Exception as exc:  # noqa: BLE001 — skip this employee only
                    for r in emp_rows:
                        record(r, tfb.FAILED, f"select employee: {type(exc).__name__}: {exc}"[:300],
                               ui.evidence("select-employee"))
                    page.keyboard.press("Escape")
                    continue
                for r in sorted(emp_rows, key=lambda x: x["date"]):
                    date = datetime.date.fromisoformat(r["date"])
                    new_entry = r["kind"] == "no_entry"
                    expect_in = _expect_in(r)
                    if new_entry and not (r.get("in_time") and r.get("out_time")):
                        record(r, tfb.FAILED, "a new entry needs both In and Out times")
                        continue
                    if not new_entry and (
                        r["kind"] not in ("missing_out", "missing_out_after_break") or not expect_in
                    ):
                        record(r, tfb.FAILED, f"only open clock-outs and missing entries can be "
                                              f"written (kind={r['kind']})")
                        continue
                    if not (month.pay_period_start <= date <= month.pay_period_end):
                        record(r, tfb.FAILED,
                               f"{date} is outside ADP's open pay period "
                               f"{month.pay_period_start}..{month.pay_period_end}")
                        continue
                    rule = r["rule"] if r["action"] == "accept" else None
                    if new_entry:
                        res = tfb.apply_new_entry(
                            ui, date=date, in_time=r["in_time"], out_time=r["out_time"],
                            comment=tfb.comment_text(
                                in_time=r["in_time"], out_time=r["out_time"],
                                decided_by=r["decided_by"] or "operator", rule=rule,
                            ),
                        )
                        record(r, res.status, res.error, res.evidence_path)
                        continue
                    res = tfb.apply_out_punch(
                        ui,
                        date=date,
                        open_entry_index=int(r["open_entry_index"]),
                        expect_in=expect_in,
                        out_time=r["out_time"],
                        comment=tfb.comment_text(
                            out_time=r["out_time"],
                            decided_by=r["decided_by"] or "operator",
                            rule=rule,
                        ),
                    )
                    record(r, res.status, res.error, res.evidence_path)
            if applied:
                try:
                    missing = _resync_in_session(
                        page, dashboard_url, store, applied, counts["max_date"])
                    counts["export_on_disk"] = True
                    why = (f"saved in ADP but not in the Timecard export after "
                           f"{RESYNC_ATTEMPTS} downloads — Sync ADP later")
                except Exception as exc:  # noqa: BLE001 — unverified is not applied
                    missing = applied
                    why = f"saved in ADP; export download failed: {type(exc).__name__}: {exc}"[:300]
                for r in missing:
                    counts[tfb.APPLIED] -= 1
                    counts[NOT_IN_HOURS] += 1
                    _set_status(client, r["decision_id"], NOT_IN_HOURS, why)
                    print(f"[punch-fix] NOT_IN_HOURS date={r['date']} emp={r['employee_id']!r} "
                          f"error={why!r}")
    finally:
        _fail_leftovers(client, store, ids, "apply job ended before this entry was written")
    print(f"[punch-fix] done {counts}")
    return counts


def run_from_env(store: str) -> dict:
    ids = [i for i in (os.environ.get("BHAGA_PUNCH_FIX_DECISION_IDS") or "").split(",") if i.strip()]
    if not ids:
        raise RuntimeError("BHAGA_PUNCH_FIX_DECISION_IDS is empty")
    return apply_decisions(store, [i.strip() for i in ids])
