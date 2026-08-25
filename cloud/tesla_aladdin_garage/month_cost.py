"""Current-month Cursor spend from the Jarvis PR cost ledger (BigQuery).

Cloud Run cannot read the laptop Cursor session DB. The hosted source of truth
is `jarvis_dev.pr_cost_build_session` + `pr_cost_review_run` (same numbers as
the Grafana Jarvis development dashboard). Fail-open: callers treat errors as
unavailable and still send the garage email.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("tesla_aladdin_garage")

PROJECT_ID = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "jarvis-bhaga-prod"
DATASET = os.environ.get("JARVIS_DEV_BQ_DATASET", "jarvis_dev")
TZ = "America/Chicago"
DEFAULT_BUDGET_USD = 10.0

_MONTH_SQL = """
SELECT
  COALESCE(SUM(cost_usd), 0) AS usd
FROM (
  SELECT cost_usd, SAFE.TIMESTAMP(ts) AS t
  FROM `{project}.{dataset}.pr_cost_build_session`
  UNION ALL
  SELECT cost_usd, SAFE.TIMESTAMP(ts) AS t
  FROM `{project}.{dataset}.pr_cost_review_run`
)
WHERE t >= TIMESTAMP(DATE_TRUNC(CURRENT_DATE('{tz}'), MONTH), '{tz}')
  AND t < TIMESTAMP(DATE_ADD(DATE_TRUNC(CURRENT_DATE('{tz}'), MONTH), INTERVAL 1 MONTH), '{tz}')
"""


def budget_usd() -> float:
    raw = os.environ.get("CURSOR_MONTH_BUDGET_USD", str(DEFAULT_BUDGET_USD))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_BUDGET_USD


def format_cursor_cost_lines(info: dict[str, Any]) -> list[str]:
    budget = float(info.get("budget_usd") or DEFAULT_BUDGET_USD)
    month = info.get("month") or "this month"
    if not info.get("ok"):
        err = f" ({info.get('error')})" if info.get("error") else ""
        return [
            f"Cursor spend ({month}, Jarvis PR ledger): unavailable{err}",
            f"Cap: ${budget:.2f}/month. Check Grafana if this stays unavailable.",
        ]
    usd = float(info["usd"])
    remaining = budget - usd
    flag = "OVER CAP" if remaining < 0 else "within cap"
    leftover = f"-${abs(remaining):.2f}" if remaining < 0 else f"${remaining:.2f}"
    return [
        f"Cursor spend ({month}, Jarvis PR ledger): ${usd:.2f} / ${budget:.2f} ({flag})",
        f"Remaining vs cap: {leftover}. This is billed Cursor usage captured into BigQuery, not Tesla/Aladdin.",
    ]


def format_cursor_cost_subject(info: dict[str, Any]) -> str:
    if not info.get("ok"):
        return "Cursor n/a"
    usd = float(info["usd"])
    budget = float(info.get("budget_usd") or DEFAULT_BUDGET_USD)
    return f"Cursor ${usd:.2f}/${budget:.0f}"


def month_cursor_cost(*, query_fn=None) -> dict[str, Any]:
    """Return {ok, usd, budget_usd, month, error}."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo(TZ))
    month = now.strftime("%Y-%m")
    budget = budget_usd()
    try:
        usd = float((query_fn or _query_month_usd)())
    except Exception as e:  # noqa: BLE001 — notify must fail-open
        log.error("tesla-aladdin-garage fail reason=cursor_month_cost err=%s", e)
        return {
            "ok": False,
            "usd": None,
            "budget_usd": budget,
            "month": month,
            "error": type(e).__name__,
        }
    return {"ok": True, "usd": usd, "budget_usd": budget, "month": month, "error": ""}


def _query_month_usd() -> float:
    from google.auth.transport.requests import AuthorizedSession
    import google.auth

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"])
    session = AuthorizedSession(creds)
    sql = _MONTH_SQL.format(project=PROJECT_ID, dataset=DATASET, tz=TZ)
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT_ID}/queries"
    resp = session.post(
        url, json={"query": sql, "useLegacySql": False, "timeoutMs": 20000}, timeout=25
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"bq_http_{resp.status_code}")
    data = resp.json()
    rows = data.get("rows") or []
    if not rows:
        return 0.0
    return float(rows[0]["f"][0]["v"])
