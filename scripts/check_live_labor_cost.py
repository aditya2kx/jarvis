#!/usr/bin/env python3
"""CI/local gate: labor $ / % must not be read from frozen model_labor_* dollars.

Issue #267: a bad pay_info scrape wrote ~$1.25 rates into adp_wage_rates, the
nightly materialize froze those dollars into model_labor_daily, and restoring
rates did not fix Labor/Home/Grafana % until FORCE_MODEL_RECOMPUTE. Presentation
must use vw_labor_daily_live / vw_labor_weekly_live (current rates × shifts) or
an in-query adp_shifts × adp_wage_rates join (hour grain).

Fails if a console BQ query or Grafana panel selects hourly/fulltime/total
labor_cost from vw_model_labor_daily / vw_model_labor_weekly.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
_QUERIES = _REPO / "apps" / "operator-console" / "lib" / "bq" / "queries.ts"
_DASHBOARD = _REPO / "agents" / "bhaga" / "grafana" / "dashboard.json"
_MIGRATION = _REPO / "core" / "migrations" / "069_labor_cost_live_rates.sql"

COST_RE = re.compile(
    r"\b(hourly_labor_cost|fulltime_labor_cost|total_labor_cost)\b"
)
FROZEN_RE = re.compile(r"\bvw_model_labor_(daily|weekly)\b")
LIVE_RE = re.compile(r"\bvw_labor_(daily|weekly)_live\b")
COMMENT_LINE = re.compile(r"^\s*//.*$", re.MULTILINE)
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_ts_comments(src: str) -> str:
    return COMMENT_LINE.sub("", BLOCK_COMMENT.sub("", src))


def _frozen_labor_cost(sql: str) -> bool:
    return bool(COST_RE.search(sql) and FROZEN_RE.search(sql))


def _grafana_labor_cost_not_live(sql: str) -> bool:
    return bool(COST_RE.search(sql) and not LIVE_RE.search(sql))


def main() -> int:
    errors: list[str] = []

    if not _MIGRATION.is_file():
        errors.append(f"missing {_MIGRATION.relative_to(_REPO)}")
    else:
        mig = _MIGRATION.read_text()
        if "vw_labor_daily_live" not in mig or "vw_labor_weekly_live" not in mig:
            errors.append("069_labor_cost_live_rates.sql must define vw_labor_daily_live and vw_labor_weekly_live")
        if "adp_wage_rates" not in mig or "adp_shifts" not in mig:
            errors.append("069 live views must join adp_shifts × adp_wage_rates")

    queries = _strip_ts_comments(_QUERIES.read_text())
    if "vw_labor_daily_live" not in queries:
        errors.append("queries.ts must read vw_labor_daily_live for day/week labor $")
    for i, chunk in enumerate(re.split(r"\nexport (?:async )?function ", queries)):
        if _frozen_labor_cost(chunk):
            errors.append(
                f"queries.ts function #{i} selects labor $ from vw_model_labor_daily/weekly — "
                "use vw_labor_daily_live (or adp_shifts × adp_wage_rates for hour grain)"
            )

    dashboard = json.loads(_DASHBOARD.read_text())
    for panel in dashboard.get("panels", []):
        if panel.get("type") in ("row", "text"):
            continue
        pid = panel.get("id")
        title = panel.get("title", "")
        for target in panel.get("targets") or []:
            sql = target.get("rawSql") or ""
            if _frozen_labor_cost(sql):
                errors.append(
                    f"Grafana panel {pid} ({title!r}) reads labor $ from frozen "
                    "vw_model_labor_daily/weekly — use vw_labor_*_live"
                )
            elif _grafana_labor_cost_not_live(sql):
                errors.append(
                    f"Grafana panel {pid} ({title!r}) computes labor $ without "
                    "vw_labor_daily_live / vw_labor_weekly_live"
                )

    if errors:
        print("check_live_labor_cost: FAIL")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("check_live_labor_cost: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
