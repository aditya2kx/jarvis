"""BigQuery datastore for BHAGA — canonical structured data store.

Uses Application Default Credentials (ADC), same SA as Cloud Run.
Provides: schema migrations, read/write helpers.

Gated by BHAGA_DATASTORE env var: set to "bigquery" to enable.
When unset or set to anything else, all operations gracefully no-op
(returns empty lists, skips writes) so the laptop flow keeps working
without a BigQuery dependency.
"""

from __future__ import annotations

import datetime
import logging
import os
import pathlib
import re
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ID = "jarvis-bhaga-prod"
# BQ dataset is env-driven so sandbox runs write to an ISOLATED dataset
# (BHAGA_BQ_DATASET=bhaga_sandbox) instead of polluting prod `bhaga`. Defaults
# to prod. Resolved at import; every process (nightly, sandbox, backfill) sets
# the env before launching python, so import-time resolution is correct.
_DEFAULT_DATASET = "bhaga"
_DATASET = os.environ.get("BHAGA_BQ_DATASET", _DEFAULT_DATASET)
_MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"


def dataset() -> str:
    """The active BQ dataset (prod `bhaga` unless BHAGA_BQ_DATASET overrides)."""
    return _DATASET


def fq(table: str) -> str:
    """Fully-qualified, backtick-quoted table ref for the active dataset.

    Use this instead of hardcoding `jarvis-bhaga-prod.bhaga.<table>` so sandbox
    runs (BHAGA_BQ_DATASET=bhaga_sandbox) target the isolated dataset.
    """
    return f"`{_PROJECT_ID}.{_DATASET}.{table}`"


def _is_enabled() -> bool:
    return os.environ.get("BHAGA_DATASTORE", "").lower() == "bigquery"


def _assert_sandbox_write_isolation() -> None:
    """Hard guard: a sandbox/staging run must never WRITE to the prod dataset.

    Mirrors ``gcs_cache._assert_sandbox_write_isolation`` for BigQuery. A sandbox
    run (``BHAGA_SHEET_MODE=staging``) may READ prod tables but must write only to
    its isolated dataset (``BHAGA_BQ_DATASET=bhaga_sandbox``). This is the missing
    guard that previously let a sandbox test row leak into prod BQ.
    """
    if os.environ.get("BHAGA_SHEET_MODE", "").lower() != "staging":
        return
    if _DATASET == _DEFAULT_DATASET:
        raise RuntimeError(
            f"BLOCKED: a sandbox/staging run attempted to WRITE to the production "
            f"BigQuery dataset '{_DEFAULT_DATASET}'. Set BHAGA_BQ_DATASET to a sandbox "
            f"dataset. Sandbox runs may READ prod data but must NEVER write it "
            f"(see .cursor/rules/bhaga-principles.mdc — sandbox isolation)."
        )


def ensure_dataset() -> None:
    """Create the active dataset if it doesn't exist (idempotent).

    Lets a sandbox/one-off point BHAGA_BQ_DATASET at a fresh dataset and have it
    materialized on first ensure_schema(). No-op when BQ is disabled.
    """
    client = get_client()
    if client is None:
        return
    from google.cloud import bigquery
    ds_ref = bigquery.Dataset(f"{_PROJECT_ID}.{_DATASET}")
    ds_ref.location = "US"
    client.create_dataset(ds_ref, exists_ok=True)


def get_client():
    """Return a BigQuery Client, or None if BQ is disabled/unavailable.

    Auth priority:
      1. ADC (GOOGLE_APPLICATION_CREDENTIALS env var, or metadata server)
      2. gcloud CLI user credentials (via `gcloud auth print-access-token`)
    """
    if not _is_enabled():
        return None
    try:
        from google.cloud import bigquery
        try:
            return bigquery.Client(project=_PROJECT_ID)
        except Exception:
            pass
        creds = _gcloud_credentials()
        if creds is not None:
            return bigquery.Client(project=_PROJECT_ID, credentials=creds)
        logger.warning("No BigQuery credentials available")
        return None
    except Exception:
        logger.warning("BigQuery client init failed; falling back to no-op", exc_info=True)
        return None


def _gcloud_credentials():
    """Build credentials from the active gcloud CLI session."""
    import subprocess
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token", f"--project={_PROJECT_ID}"],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
        ).strip()
        if not token:
            return None
        from google.oauth2.credentials import Credentials
        return Credentials(token=token)
    except Exception:
        return None


def ensure_schema() -> list[str]:
    """Run pending SQL migrations from core/migrations/ in version order.

    Migration files are named NNN_description.sql (e.g. 001_initial_schema.sql).
    Returns list of newly-applied migration names.
    """
    client = get_client()
    if client is None:
        return []

    # Make sure the target dataset exists (matters for an isolated sandbox
    # dataset on first run; no-op for the already-existing prod dataset).
    ensure_dataset()

    applied = _get_applied_versions(client)
    pending = _scan_migration_files()
    newly_applied: list[str] = []

    for version, name, path in sorted(pending, key=lambda t: t[0]):
        if version in applied:
            continue
        sql = _rewrite_dataset(path.read_text())
        logger.info("Applying migration %03d_%s into %s ...", version, name, _DATASET)
        for statement in _split_statements(sql):
            statement = statement.strip()
            if not statement:
                continue
            client.query(statement).result()
        client.query(
            f"INSERT INTO `{_PROJECT_ID}.{_DATASET}._schema_migrations` "
            f"(version, name, applied_at) VALUES (@v, @n, CURRENT_TIMESTAMP())",
            job_config=_param_config([
                ("v", "INT64", version),
                ("n", "STRING", name),
            ]),
        ).result()
        newly_applied.append(f"{version:03d}_{name}")
        logger.info("  Applied %03d_%s", version, name)

    return newly_applied


def load_rows(
    table_name: str,
    rows: list[dict],
    *,
    merge_keys: list[str] | None = None,
    column_bq_types: dict[str, str] | None = None,
    replace: bool = False,
) -> int:
    """Bulk-load rows into a BigQuery table.

    Three modes:

    * ``replace=True`` — TRUNCATE the table, then INSERT every row. This is the
      correct mode for a **fresh full-history scrape/backfill**: the scrape is
      authoritative for the whole window, so the table is emptied and rebuilt.
      It also sidesteps the "MERGE must match at most one source row per target
      row" error that a MERGE raises when a single scrape batch legitimately
      contains multiple rows sharing a natural key (e.g. several ADP earnings
      line-items for the same employee/period/description). ``merge_keys`` is
      ignored in this mode. Use ONLY when the incoming rows cover the table's
      full intended contents — a windowed replace would drop out-of-window rows.
    * ``merge_keys`` given — MERGE (idempotent upsert) on those columns. This is
      the nightly/incremental default: re-running converges values, never dupes.
    * neither — a plain INSERT (append).

    column_bq_types: optional {col: bq_type} override for columns whose type
    cannot be inferred from the data (e.g. all-None batches). Takes priority
    over inferred types.

    Returns number of rows affected. NOTE: data ALWAYS lands directly in
    BigQuery here — BQ is the single system of record. Nothing in this path
    reads from or writes data files to GCS; GCS holds only browser sessions and
    failure evidence (see agents/bhaga/scripts/gcs_cache.py).
    """
    client = get_client()
    if client is None or not rows:
        return 0

    _assert_sandbox_write_isolation()
    fq_table = f"`{_PROJECT_ID}.{_DATASET}.{table_name}`"
    columns = list(rows[0].keys())

    if replace:
        # Empty the table first so the fresh scrape fully owns its contents.
        client.query(f"TRUNCATE TABLE {fq_table}").result()
        return _insert_rows(client, fq_table, columns, rows, column_bq_types or {})
    if merge_keys:
        return _merge_rows(client, fq_table, columns, rows, merge_keys, column_bq_types or {})
    return _insert_rows(client, fq_table, columns, rows, column_bq_types or {})


def read_table(table_name: str, *, where: str = "", limit: int | None = None) -> list[dict]:
    """Read all rows from a table (with optional WHERE clause)."""
    sql = f"SELECT * FROM `{_PROJECT_ID}.{_DATASET}.{table_name}`"
    if where:
        sql += f" WHERE {where}"
    if limit is not None:
        sql += f" LIMIT {limit}"
    return read_query(sql)


def read_query(sql: str) -> list[dict]:
    """Run arbitrary SQL and return results as list[dict].

    An access/permission error (e.g. the orchestrator SA missing
    ``bigquery.jobUser``/``bigquery.dataEditor``) is **re-raised**, never
    swallowed into an empty result: a silent ``[]`` masks the real cause and
    surfaces downstream as a misleading crash (e.g. ``max() iterable argument is
    empty`` in ``materialize_model_bq``). All other errors keep the lenient
    no-op so the migration bootstrap (querying a not-yet-created table) still
    degrades to ``[]``.
    """
    client = get_client()
    if client is None:
        return []
    try:
        result = client.query(sql).result()
        return [dict(row) for row in result]
    except Exception as exc:
        if _is_access_error(exc):
            logger.error(
                "BigQuery access denied — the caller's identity lacks BigQuery "
                "permission. The orchestrator SA needs roles/bigquery.jobUser + "
                "roles/bigquery.dataEditor on %s. See RUNBOOK §7/§14.",
                _PROJECT_ID,
                exc_info=True,
            )
            raise
        logger.warning("BigQuery query failed", exc_info=True)
        return []


def _is_access_error(exc: BaseException) -> bool:
    """True if ``exc`` is a BigQuery access/permission/auth error (403/401).

    Matched structurally (HTTP status / exception type) so it survives an
    upgrade of google-cloud-bigquery without depending on message text.
    """
    try:
        from google.api_core import exceptions as gexc

        if isinstance(exc, (gexc.Forbidden, gexc.Unauthorized, gexc.PermissionDenied)):
            return True
    except Exception:  # noqa: BLE001 — google libs optional / import-time issues
        pass
    return getattr(exc, "code", None) in (401, 403)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_applied_versions(client) -> set[int]:
    """Read _schema_migrations to find already-applied versions."""
    try:
        rows = client.query(
            f"SELECT version FROM `{_PROJECT_ID}.{_DATASET}._schema_migrations`"
        ).result()
        return {row.version for row in rows}
    except Exception:
        return set()


def _rewrite_dataset(sql: str) -> str:
    """Point migration DDL at the active dataset when it isn't the prod default.

    Migration files hardcode the `bhaga.` dataset qualifier (both bare
    `bhaga.<table>` and `jarvis-bhaga-prod.bhaga.<table>`). Replacing the
    substring ``bhaga.`` is safe: the project id ``jarvis-bhaga-prod`` contains
    ``bhaga-`` (hyphen, no dot), so only dataset qualifiers are rewritten.
    """
    if _DATASET == _DEFAULT_DATASET:
        return sql
    return sql.replace(f"{_DEFAULT_DATASET}.", f"{_DATASET}.")


def _scan_migration_files() -> list[tuple[int, str, pathlib.Path]]:
    """Scan migrations/ for NNN_name.sql files. Returns [(version, name, path)]."""
    pattern = re.compile(r"^(\d+)_(.+)\.sql$")
    results = []
    if not _MIGRATIONS_DIR.is_dir():
        return results
    for f in sorted(_MIGRATIONS_DIR.iterdir()):
        m = pattern.match(f.name)
        if m:
            results.append((int(m.group(1)), m.group(2), f))
    return results


def _split_statements(sql: str) -> list[str]:
    """Split a SQL file into individual statements.

    Splits on semicolons that are NOT inside a line comment (-- ...), a
    block comment (/* ... */), or a single-quoted string literal ('...').
    Single-quoted strings handle escaped quotes via doubling ('').
    """
    statements: list[str] = []
    current: list[str] = []
    i = 0
    n = len(sql)
    in_line_comment = False
    in_block_comment = False
    in_string = False  # inside a single-quoted SQL string literal

    while i < n:
        ch = sql[i]

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            current.append(ch)
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and i + 1 < n and sql[i + 1] == "/":
                current.append("*/")
                i += 2
                in_block_comment = False
            else:
                current.append(ch)
                i += 1
            continue

        if in_string:
            current.append(ch)
            i += 1
            if ch == "'":
                # SQL escaped quote: '' is a literal single quote, not end-of-string.
                if i < n and sql[i] == "'":
                    current.append(sql[i])
                    i += 1
                else:
                    in_string = False
            continue

        if ch == "'":
            in_string = True
            current.append(ch)
            i += 1
            continue

        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            in_line_comment = True
            current.append("--")
            i += 2
            continue

        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            in_block_comment = True
            current.append("/*")
            i += 2
            continue

        if ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)

    return statements


def _insert_rows(
    client,
    fq_table: str,
    columns: list[str],
    rows: list[dict],
    column_bq_types_hint: dict[str, str] | None = None,
) -> int:
    """Simple INSERT of every row (no merge — duplicate keys are preserved).

    Per-column BQ types are resolved hint-first, then from the first non-None
    value in the batch, else STRING — so an all-None column (e.g. a nullable
    rate in one batch) is typed correctly instead of defaulting to a type that
    rejects the NULL. This mirrors the typing logic in ``_merge_rows``.
    """
    col_list = ", ".join(columns)
    hints = column_bq_types_hint or {}

    inserted = 0
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]

        col_types: dict[str, str] = {}
        for c in columns:
            if c in hints:
                col_types[c] = hints[c]
                continue
            for row in batch:
                v = row.get(c)
                if v is not None:
                    col_types[c] = _infer_bq_type(v)
                    break
            else:
                col_types[c] = "STRING"

        values_clauses = []
        params = []
        for idx, row in enumerate(batch):
            field_refs = []
            for c in columns:
                val = row.get(c)
                if val is None:
                    field_refs.append(f"CAST(NULL AS {col_types[c]})")
                else:
                    field_refs.append(f"@{c}_{idx}")
                    params.append((f"{c}_{idx}", col_types[c], val))
            values_clauses.append(f"({', '.join(field_refs)})")

        sql = f"INSERT INTO {fq_table} ({col_list}) VALUES {', '.join(values_clauses)}"
        try:
            client.query(sql, job_config=_param_config(params)).result()
            inserted += len(batch)
        except Exception:
            logger.error("Insert batch failed for %s (batch %d)", fq_table, i // batch_size, exc_info=True)
            raise

    return inserted


def _merge_rows(
    client,
    fq_table: str,
    columns: list[str],
    rows: list[dict],
    merge_keys: list[str],
    column_bq_types_hint: dict[str, str] | None = None,
) -> int:
    """MERGE (upsert) rows into the target table.

    NULL handling: infers the BQ type for each column from the first non-None
    value in the batch.  NULL values are emitted as CAST(NULL AS <type>) in
    the SQL rather than as @parameters so that UNION ALL sees uniform types.

    column_bq_types_hint: explicit type overrides that take priority, used when
    all values in a batch are None (so inference falls back to STRING).
    """
    from google.cloud import bigquery

    non_key_cols = [c for c in columns if c not in merge_keys]
    hints = column_bq_types_hint or {}

    merged = 0
    batch_size = 200
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]

        # Determine the canonical BQ type for each column; hints take priority
        col_types: dict[str, str] = {}
        for c in columns:
            if c in hints:
                col_types[c] = hints[c]
                continue
            for row in batch:
                v = row.get(c)
                if v is not None:
                    col_types[c] = _infer_bq_type(v)
                    break
            else:
                col_types[c] = "STRING"

        source_rows = []
        params = []
        for idx, row in enumerate(batch):
            fields = []
            for c in columns:
                val = row.get(c)
                if val is None:
                    fields.append(f"CAST(NULL AS {col_types[c]}) AS {c}")
                else:
                    param_name = f"{c}_{idx}"
                    fields.append(f"@{param_name} AS {c}")
                    params.append((param_name, col_types[c], val))
            source_rows.append(f"SELECT {', '.join(fields)}")

        source_sql = " UNION ALL ".join(source_rows)
        on_clause = " AND ".join(f"T.{k} = S.{k}" for k in merge_keys)
        update_clause = ", ".join(f"T.{c} = S.{c}" for c in non_key_cols) if non_key_cols else "T._noop = 1"
        insert_cols = ", ".join(columns)
        insert_vals = ", ".join(f"S.{c}" for c in columns)

        sql = (
            f"MERGE {fq_table} T "
            f"USING ({source_sql}) S "
            f"ON {on_clause} "
            f"WHEN MATCHED THEN UPDATE SET {update_clause} "
            f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
        )
        try:
            result = client.query(sql, job_config=_param_config(params)).result()
            merged += len(batch)
        except Exception:
            logger.error("Merge batch failed for %s (batch %d)", fq_table, i // batch_size, exc_info=True)
            raise

    return merged


def _infer_bq_type(value: Any) -> str:
    """Map a Python value to a BigQuery scalar type name for query parameters."""
    if value is None:
        return "STRING"
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "FLOAT64"
    if isinstance(value, datetime.datetime):
        return "TIMESTAMP"
    if isinstance(value, datetime.date):
        return "DATE"
    return "STRING"


def _param_config(params: list[tuple[str, str, Any]]):
    """Build a QueryJobConfig with scalar query parameters."""
    from google.cloud import bigquery

    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter(name, type_, val)
            for name, type_, val in params
        ]
    )
