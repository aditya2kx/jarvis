#!/usr/bin/env python3
"""Jarvis-level BigQuery store for the PR cost ledger.

Self-contained and INDEPENDENT of BHAGA's core.datastore: its own client, its
own dataset (jarvis_dev), and its own schema bootstrap. PR cost is a Jarvis dev
metric, not BHAGA domain data, so it never touches the `bhaga` dataset or the
core/migrations chain.

Auth: Application Default Credentials (ADC) - works in CI via Workload Identity
and locally via `gcloud auth application-default login`; falls back to a token
from the active `gcloud` CLI session.

The record dict shape is identical to pr_cost_ledger._empty_record(), so the
ledger's renderers/analyzers are reused unchanged.
"""
from __future__ import annotations

import json
import os
from typing import Any

PROJECT_ID = "jarvis-bhaga-prod"
DATASET = os.environ.get("JARVIS_DEV_BQ_DATASET", "jarvis_dev")

_T_PR = "pr_cost_pr"
_T_BUILD = "pr_cost_build_session"
_T_REVIEW = "pr_cost_review_run"
_V_TOTALS = "vw_pr_cost"


class PrCostStoreError(RuntimeError):
    pass


def pr_key(pr: int | str) -> str:
    """Stable string key. Numeric -> the PR number; else 'branch:<raw>'."""
    if isinstance(pr, int) or (isinstance(pr, str) and str(pr).isdigit()):
        return str(int(pr))
    return f"branch:{pr}"


def _client():
    from google.cloud import bigquery
    try:
        return bigquery.Client(project=PROJECT_ID)
    except Exception:
        pass
    import subprocess
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token", f"--project={PROJECT_ID}"],
            text=True, stderr=subprocess.DEVNULL, timeout=15,
        ).strip()
    except Exception as exc:  # noqa: BLE001
        raise PrCostStoreError(
            "No BigQuery credentials. Run `gcloud auth application-default login` "
            "locally, or ensure WIF auth in CI."
        ) from exc
    if not token:
        raise PrCostStoreError("empty gcloud access token")
    from google.oauth2.credentials import Credentials
    from google.cloud import bigquery  # noqa: F811
    return bigquery.Client(project=PROJECT_ID, credentials=Credentials(token=token))


def _fq(table: str) -> str:
    return f"`{PROJECT_ID}.{DATASET}.{table}`"


def ensure_schema() -> None:
    """Idempotent: create dataset + 3 tables + view. Safe to call every run."""
    from google.cloud import bigquery
    c = _client()
    ds = bigquery.Dataset(f"{PROJECT_ID}.{DATASET}")
    ds.location = "US"
    c.create_dataset(ds, exists_ok=True)
    stmts = [
        f"""CREATE TABLE IF NOT EXISTS {_fq(_T_PR)} (
          pr_key STRING NOT NULL, pr_number INT64, provisional_id STRING,
          title STRING, requirement STRING, branch STRING,
          created_at STRING, merged_at STRING, session_started_at STRING,
          diff_files INT64, diff_additions INT64, diff_deletions INT64,
          build_source STRING, build_approximate BOOL, build_attribution_mode STRING,
          build_window_start STRING, build_window_end STRING,
          conversation_ids STRING, review_source STRING,
          updated_at TIMESTAMP )""",
        f"""CREATE TABLE IF NOT EXISTS {_fq(_T_BUILD)} (
          pr_key STRING NOT NULL, session_uid STRING NOT NULL,
          ts STRING, model STRING, tokens INT64, cost_usd FLOAT64,
          cost_source STRING, conversation_id STRING,
          input_tokens INT64, output_tokens INT64,
          cache_read_input_tokens INT64, cache_creation_input_tokens INT64,
          note STRING )""",
        f"""CREATE TABLE IF NOT EXISTS {_fq(_T_REVIEW)} (
          pr_key STRING NOT NULL, review_uid STRING NOT NULL,
          ts STRING, model STRING, turns INT64,
          input_tokens INT64, output_tokens INT64,
          cache_read_input_tokens INT64, cache_creation_input_tokens INT64,
          tokens INT64, cost_usd FLOAT64, result STRING, run_url STRING )""",
        f"""CREATE OR REPLACE VIEW {_fq(_V_TOTALS)} AS
          SELECT p.pr_key, p.pr_number, p.title, p.requirement, p.branch,
                 p.created_at, p.merged_at,
                 COALESCE(b.build_cost,0.0) AS build_cost_usd,
                 COALESCE(b.build_tokens,0) AS build_tokens,
                 COALESCE(r.review_cost,0.0) AS review_cost_usd,
                 COALESCE(r.review_tokens,0) AS review_tokens,
                 COALESCE(r.review_runs,0) AS review_runs,
                 COALESCE(b.build_cost,0.0)+COALESCE(r.review_cost,0.0) AS total_cost_usd
          FROM {_fq(_T_PR)} p
          LEFT JOIN (SELECT pr_key, SUM(cost_usd) build_cost, SUM(tokens) build_tokens
                     FROM {_fq(_T_BUILD)} GROUP BY pr_key) b USING (pr_key)
          LEFT JOIN (SELECT pr_key, SUM(cost_usd) review_cost, SUM(tokens) review_tokens,
                            COUNT(*) review_runs
                     FROM {_fq(_T_REVIEW)} GROUP BY pr_key) r USING (pr_key)
          WHERE p.merged_at IS NOT NULL""",
    ]
    for s in stmts:
        c.query(s).result()


def _session_uid(s: dict[str, Any]) -> str:
    import hashlib
    raw = f"{s.get('ts') or ''}|{round(float(s.get('cost_usd') or 0), 4)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _review_uid(r: dict[str, Any]) -> str:
    import hashlib
    raw = (r.get("run_url") or r.get("ts")
           or json.dumps([r.get(k) for k in (
               "model", "turns", "result", "input_tokens", "output_tokens",
               "cache_read_input_tokens", "cache_creation_input_tokens", "cost_usd")]))
    return hashlib.sha1(str(raw).encode()).hexdigest()[:16]


def save_record(rec: dict[str, Any]) -> None:
    """Full overwrite of one PR's rows (parent MERGE + children replace).

    Mirrors the old file write: this PR fully owns its child rows, so we DELETE
    then INSERT children (no stale rows) and MERGE the parent.
    """
    import datetime
    from google.cloud import bigquery
    ensure_schema()
    c = _client()
    key = pr_key(rec.get("pr_number") if rec.get("pr_number") is not None
                 else rec.get("provisional_id"))
    b, r = rec["build"], rec["review"]
    parent = {
        "pr_key": key,
        "pr_number": rec.get("pr_number"),
        "provisional_id": rec.get("provisional_id"),
        "title": rec.get("title"), "requirement": rec.get("requirement"),
        "branch": rec.get("branch"), "created_at": rec.get("created_at"),
        "merged_at": rec.get("merged_at"),
        "session_started_at": rec.get("session_started_at"),
        "diff_files": (rec.get("diff") or {}).get("files"),
        "diff_additions": (rec.get("diff") or {}).get("additions"),
        "diff_deletions": (rec.get("diff") or {}).get("deletions"),
        "build_source": b.get("source"), "build_approximate": b.get("approximate"),
        "build_attribution_mode": b.get("attribution_mode"),
        "build_window_start": (b.get("window") or {}).get("start"),
        "build_window_end": (b.get("window") or {}).get("end"),
        "conversation_ids": json.dumps(b.get("conversation_ids") or []),
        "review_source": r.get("source"),
        "updated_at": datetime.datetime.now(datetime.timezone.utc),
    }
    # Parent MERGE
    cols = list(parent.keys())
    set_clause = ", ".join(f"T.{c2}=S.{c2}" for c2 in cols if c2 != "pr_key")
    sel = ", ".join(f"@{c2} AS {c2}" for c2 in cols)
    params = [_p(c2, parent[c2], _PARENT_COL_TYPES.get(c2)) for c2 in cols]
    c.query(
        f"MERGE {_fq(_T_PR)} T USING (SELECT {sel}) S ON T.pr_key=S.pr_key "
        f"WHEN MATCHED THEN UPDATE SET {set_clause} "
        f"WHEN NOT MATCHED THEN INSERT ({', '.join(cols)}) VALUES ({', '.join('S.'+c2 for c2 in cols)})",
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()
    # Children: delete + insert
    c.query(f"DELETE FROM {_fq(_T_BUILD)} WHERE pr_key=@k",
            job_config=_cfg([_p("k", key)])).result()
    c.query(f"DELETE FROM {_fq(_T_REVIEW)} WHERE pr_key=@k",
            job_config=_cfg([_p("k", key)])).result()
    for s in b.get("sessions") or []:
        row = {"pr_key": key, "session_uid": _session_uid(s),
               "ts": s.get("ts"), "model": s.get("model"),
               "tokens": int(s.get("tokens") or 0),
               "cost_usd": float(s.get("cost_usd") or 0),
               "cost_source": s.get("cost_source"),
               "conversation_id": s.get("conversation_id"),
               "input_tokens": _int_or_none(s.get("input_tokens")),
               "output_tokens": _int_or_none(s.get("output_tokens")),
               "cache_read_input_tokens": _int_or_none(s.get("cache_read_input_tokens")),
               "cache_creation_input_tokens": _int_or_none(s.get("cache_creation_input_tokens")),
               "note": s.get("note")}
        _insert(c, _T_BUILD, row, _BUILD_COL_TYPES)
    for x in r.get("runs") or []:
        row = {"pr_key": key, "review_uid": _review_uid(x),
               "ts": x.get("ts"), "model": x.get("model"),
               "turns": _int_or_none(x.get("turns")),
               "input_tokens": _int_or_none(x.get("input_tokens")),
               "output_tokens": _int_or_none(x.get("output_tokens")),
               "cache_read_input_tokens": _int_or_none(x.get("cache_read_input_tokens")),
               "cache_creation_input_tokens": _int_or_none(x.get("cache_creation_input_tokens")),
               "tokens": _int_or_none(x.get("tokens")),
               "cost_usd": float(x.get("cost_usd") or 0) if x.get("cost_usd") is not None else None,
               "result": x.get("result"), "run_url": x.get("run_url")}
        _insert(c, _T_REVIEW, row, _REVIEW_COL_TYPES)


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    return int(v)


def load_record(pr: int | str, empty_factory) -> dict[str, Any]:
    """Read parent+children and assemble the record dict; empty_factory(pr) if absent."""
    ensure_schema()
    c = _client()
    key = pr_key(pr)
    prows = list(c.query(f"SELECT * FROM {_fq(_T_PR)} WHERE pr_key=@k",
                         job_config=_cfg([_p("k", key)])).result())
    if not prows:
        return empty_factory(pr)
    p = dict(prows[0])
    rec = empty_factory(pr)
    rec["pr_number"] = p.get("pr_number")
    rec["provisional_id"] = p.get("provisional_id")
    for k2 in ("title", "requirement", "branch", "created_at", "merged_at",
               "session_started_at"):
        rec[k2] = p.get(k2)
    rec["diff"] = {"files": p.get("diff_files"), "additions": p.get("diff_additions"),
                   "deletions": p.get("diff_deletions")}
    rec["build"]["source"] = p.get("build_source")
    rec["build"]["approximate"] = p.get("build_approximate")
    rec["build"]["attribution_mode"] = p.get("build_attribution_mode")
    if p.get("build_window_start") or p.get("build_window_end"):
        rec["build"]["window"] = {"start": p.get("build_window_start"),
                                  "end": p.get("build_window_end")}
    rec["build"]["conversation_ids"] = json.loads(p.get("conversation_ids") or "[]")
    rec["review"]["source"] = p.get("review_source") or rec["review"]["source"]
    brows = list(c.query(
        f"SELECT * FROM {_fq(_T_BUILD)} WHERE pr_key=@k ORDER BY ts",
        job_config=_cfg([_p("k", key)])).result())
    rec["build"]["sessions"] = [
        {"ts": x["ts"], "model": x["model"], "tokens": x["tokens"],
         "cost_usd": x["cost_usd"], "cost_source": x["cost_source"],
         "conversation_id": x["conversation_id"], "input_tokens": x["input_tokens"],
         "output_tokens": x["output_tokens"],
         "cache_read_input_tokens": x["cache_read_input_tokens"],
         "cache_creation_input_tokens": x["cache_creation_input_tokens"],
         "note": x["note"]} for x in brows]
    rrows = list(c.query(
        f"SELECT * FROM {_fq(_T_REVIEW)} WHERE pr_key=@k ORDER BY ts",
        job_config=_cfg([_p("k", key)])).result())
    rec["review"]["runs"] = [
        {"ts": x["ts"], "model": x["model"], "turns": x["turns"],
         "input_tokens": x["input_tokens"], "output_tokens": x["output_tokens"],
         "cache_read_input_tokens": x["cache_read_input_tokens"],
         "cache_creation_input_tokens": x["cache_creation_input_tokens"],
         "tokens": x["tokens"], "cost_usd": x["cost_usd"], "result": x["result"],
         "run_url": x["run_url"]} for x in rrows]
    return rec


def all_prs() -> list[int]:
    ensure_schema()
    c = _client()
    rows = c.query(f"SELECT pr_number FROM {_fq(_T_PR)} "
                   f"WHERE pr_number IS NOT NULL ORDER BY pr_number").result()
    return [int(r["pr_number"]) for r in rows]


def find_duplicate_sessions(pr: int | str, session_keys: set[str]) -> list[tuple[str, Any]]:
    """Return [(other_pr_key, session_uid)] for session_keys that also appear on other PRs.

    Single BQ query instead of loading every PR's record. Returns empty list if
    session_keys is empty or if there are no duplicates.
    """
    if not session_keys:
        return []
    ensure_schema()
    c = _client()
    my_key = pr_key(pr)
    key_list = ", ".join(f"'{k}'" for k in session_keys)
    rows = c.query(
        f"SELECT pr_key, session_uid FROM {_fq(_T_BUILD)} "
        f"WHERE pr_key != @my_key AND session_uid IN ({key_list})",
        job_config=_cfg([_p("my_key", my_key)]),
    ).result()
    return [(r["pr_key"], r["session_uid"]) for r in rows]


def delete_record(pr: int | str) -> None:
    c = _client()
    key = pr_key(pr)
    for t in (_T_PR, _T_BUILD, _T_REVIEW):
        c.query(f"DELETE FROM {_fq(t)} WHERE pr_key=@k",
                job_config=_cfg([_p("k", key)])).result()


_PARENT_COL_TYPES: dict[str, str] = {
    "pr_number": "INT64", "diff_files": "INT64", "diff_additions": "INT64",
    "diff_deletions": "INT64", "build_approximate": "BOOL", "updated_at": "TIMESTAMP",
}
_BUILD_COL_TYPES: dict[str, str] = {
    "tokens": "INT64", "cost_usd": "FLOAT64", "input_tokens": "INT64",
    "output_tokens": "INT64", "cache_read_input_tokens": "INT64",
    "cache_creation_input_tokens": "INT64",
}
_REVIEW_COL_TYPES: dict[str, str] = {
    "turns": "INT64", "input_tokens": "INT64", "output_tokens": "INT64",
    "cache_read_input_tokens": "INT64", "cache_creation_input_tokens": "INT64",
    "tokens": "INT64", "cost_usd": "FLOAT64",
}


def _p(name: str, value: Any, force_type: str | None = None):
    from google.cloud import bigquery
    import datetime as _dt
    if value is None:
        t = force_type or "STRING"
        return bigquery.ScalarQueryParameter(name, t, None)
    if isinstance(value, bool):
        return bigquery.ScalarQueryParameter(name, "BOOL", value)
    if isinstance(value, int):
        return bigquery.ScalarQueryParameter(name, "INT64", value)
    if isinstance(value, float):
        return bigquery.ScalarQueryParameter(name, "FLOAT64", value)
    if isinstance(value, _dt.datetime):
        return bigquery.ScalarQueryParameter(name, "TIMESTAMP", value)
    return bigquery.ScalarQueryParameter(name, "STRING", str(value))


def _cfg(params):
    from google.cloud import bigquery
    return bigquery.QueryJobConfig(query_parameters=params)


def _insert(c, table: str, row: dict[str, Any],
            col_types: dict[str, str] | None = None) -> None:
    col_types = col_types or {}
    cols = list(row.keys())
    vals = ", ".join(f"@{k2}" for k2 in cols)
    c.query(f"INSERT INTO {_fq(table)} ({', '.join(cols)}) VALUES ({vals})",
            job_config=_cfg([_p(k2, row[k2], col_types.get(k2)) for k2 in cols])).result()


def bulk_load_records(records: list[dict[str, Any]]) -> None:
    """Fast batch load of many records via streaming inserts (for migration).

    Uses insert_rows_json for child tables (no per-row BQ job overhead) and
    a single MERGE per PR for the parent. Far faster than one INSERT per row.
    """
    import datetime
    ensure_schema()
    c = _client()

    parent_rows = []
    build_rows: list[dict] = []
    review_rows: list[dict] = []

    for rec in records:
        key = pr_key(rec.get("pr_number") if rec.get("pr_number") is not None
                     else rec.get("provisional_id"))
        b = rec.get("build") or {}
        r = rec.get("review") or {}
        parent_rows.append({
            "pr_key": key,
            "pr_number": rec.get("pr_number"),
            "provisional_id": rec.get("provisional_id"),
            "title": rec.get("title"), "requirement": rec.get("requirement"),
            "branch": rec.get("branch"), "created_at": rec.get("created_at"),
            "merged_at": rec.get("merged_at"),
            "session_started_at": rec.get("session_started_at"),
            "diff_files": (rec.get("diff") or {}).get("files"),
            "diff_additions": (rec.get("diff") or {}).get("additions"),
            "diff_deletions": (rec.get("diff") or {}).get("deletions"),
            "build_source": b.get("source"), "build_approximate": b.get("approximate"),
            "build_attribution_mode": b.get("attribution_mode"),
            "build_window_start": (b.get("window") or {}).get("start"),
            "build_window_end": (b.get("window") or {}).get("end"),
            "conversation_ids": json.dumps(b.get("conversation_ids") or []),
            "review_source": r.get("source"),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        for s in b.get("sessions") or []:
            build_rows.append({
                "pr_key": key, "session_uid": _session_uid(s),
                "ts": s.get("ts"), "model": s.get("model"),
                "tokens": int(s.get("tokens") or 0),
                "cost_usd": float(s.get("cost_usd") or 0),
                "cost_source": s.get("cost_source"),
                "conversation_id": s.get("conversation_id"),
                "input_tokens": _int_or_none(s.get("input_tokens")),
                "output_tokens": _int_or_none(s.get("output_tokens")),
                "cache_read_input_tokens": _int_or_none(s.get("cache_read_input_tokens")),
                "cache_creation_input_tokens": _int_or_none(s.get("cache_creation_input_tokens")),
                "note": s.get("note"),
            })
        for x in r.get("runs") or []:
            review_rows.append({
                "pr_key": key, "review_uid": _review_uid(x),
                "ts": x.get("ts"), "model": x.get("model"),
                "turns": _int_or_none(x.get("turns")),
                "input_tokens": _int_or_none(x.get("input_tokens")),
                "output_tokens": _int_or_none(x.get("output_tokens")),
                "cache_read_input_tokens": _int_or_none(x.get("cache_read_input_tokens")),
                "cache_creation_input_tokens": _int_or_none(x.get("cache_creation_input_tokens")),
                "tokens": _int_or_none(x.get("tokens")),
                "cost_usd": float(x.get("cost_usd") or 0) if x.get("cost_usd") is not None else None,
                "result": x.get("result"), "run_url": x.get("run_url"),
            })

    # Streaming inserts for children (fast, no per-job overhead)
    if build_rows:
        errs = c.insert_rows_json(f"{PROJECT_ID}.{DATASET}.{_T_BUILD}", build_rows)
        if errs:
            raise PrCostStoreError(f"build streaming insert errors: {errs}")
    if review_rows:
        errs = c.insert_rows_json(f"{PROJECT_ID}.{DATASET}.{_T_REVIEW}", review_rows)
        if errs:
            raise PrCostStoreError(f"review streaming insert errors: {errs}")
    # Parent: streaming insert (no dedup needed for migration, tables start empty)
    if parent_rows:
        errs = c.insert_rows_json(f"{PROJECT_ID}.{DATASET}.{_T_PR}", parent_rows)
        if errs:
            raise PrCostStoreError(f"parent streaming insert errors: {errs}")
