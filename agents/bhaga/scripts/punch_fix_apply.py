"""Write operator-approved punch fixes into ADP (Issue #356): a clock-out for an
open entry, or a whole entry for a scheduled day with no punch.

Entry point for the console "Write to ADP" button: the console flips the chosen
``punch_gap_decisions`` rows to ``applying`` and starts daily_refresh with
``BHAGA_PUNCH_FIX_APPLY_ONLY=1`` + ``BHAGA_PUNCH_FIX_DECISION_IDS``. This module
writes each one in a single ADP session and records ``applied`` /
``already_resolved`` / ``failed``. daily_refresh then runs the timecard-only
resync so ``adp_punches`` and the gaps table catch up.

Refuses to run unless ``BHAGA_PUNCH_FIX_WRITEBACK=1`` (FEATURE_FLAGS.md). A row
that is not left ``applied`` or ``already_resolved`` ends ``failed`` with a
greppable ``[punch-fix] FAIL`` breadcrumb — never retried automatically.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
from typing import Optional

from core.datastore import _param_config, fq, get_client

WRITEBACK_FLAG = "BHAGA_PUNCH_FIX_WRITEBACK"
EVIDENCE_DIR = pathlib.Path.home() / ".bhaga" / "evidence" / "punch-fix"


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


def apply_decisions(store: str, ids: list[str]) -> dict:
    """Write each ``applying`` decision; return status counts."""
    if os.environ.get(WRITEBACK_FLAG) != "1":
        raise RuntimeError(f"{WRITEBACK_FLAG}=1 is required to write to ADP")
    from skills.adp_run_automation import runner
    from skills.adp_run_automation import timecard_fix_backend as tfb
    from skills.adp_run_automation import timecard_ui_backend as tub

    client = get_client()
    rows = _load(client, store, ids)
    counts = {tfb.APPLIED: 0, tfb.ALREADY_RESOLVED: 0, tfb.FAILED: 0}
    print(f"[punch-fix] store={store} requested={len(ids)} applying={len(rows)}")
    if not rows:
        return counts

    def record(row: dict, status: str, error: Optional[str], evidence: Optional[str] = None):
        counts[status] += 1
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
    finally:
        _fail_leftovers(client, store, ids, "apply job ended before this entry was written")
    print(f"[punch-fix] done {counts}")
    return counts


def run_from_env(store: str) -> dict:
    ids = [i for i in (os.environ.get("BHAGA_PUNCH_FIX_DECISION_IDS") or "").split(",") if i.strip()]
    if not ids:
        raise RuntimeError("BHAGA_PUNCH_FIX_DECISION_IDS is empty")
    return apply_decisions(store, [i.strip() for i in ids])
