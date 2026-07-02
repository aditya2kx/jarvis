#!/usr/bin/env python3
"""
check_grafana_no_logic.py — CI/local gate: Grafana is a visualization tool
only, never a business-logic/SQL-logic layer (Issue #126).

Why this exists: the BHAGA dashboard (`agents/bhaga/grafana/dashboard.json`)
is dashboard-as-code, with every data panel's query embedded as inline
`targets[].rawSql`. Before Issue #126, the Order Assistant recommendation
(panel 81 — a full max-min water-fill allocation algorithm) and the Order
Assistant analytics table (panel 79 — TOTAL-row synthesis) carried real
business logic directly in Grafana SQL. That defeats two goals: (1) fast
panel load (Grafana should be a thin read, not a compute engine), and
(2) portability (moving visualization to another tool or a custom website
requires re-implementing whatever logic lives only in Grafana).

The fix moved that logic into BigQuery views/table-functions
(`core/migrations/029_order_assistant_functions.sql`); Grafana panels now do
a plain `SELECT * FROM <view-or-tvf>(...)`. This gate keeps that true by
construction: every *data* panel's `rawSql` must match a narrow "presentation
only" allowlist grammar, or be an explicit, tracked waiver.

Presentation-only means: a single SELECT reading from one `vw_*` view or one
`tvf_*` table function (with literal/variable args), an optional `WHERE`
restricted to an equality/date filter, and an optional `ORDER BY`. No CTEs,
no UNION, no JOIN, no correlated subqueries, no window functions, no
GENERATE_ARRAY — those are compute, not presentation.

Usage:
    python3 scripts/check_grafana_no_logic.py
    python3 scripts/check_grafana_no_logic.py --dashboard path/to/dashboard.json

Exit 0 = every panel is either clean or has a recorded waiver.
Exit 1 = a panel has banned constructs in its rawSql and no waiver — or a
         waived panel no longer needs its waiver (keeps the allowlist honest
         so waivers get removed, not accumulated forever).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DEFAULT_DASHBOARD = _REPO_ROOT / "agents" / "bhaga" / "grafana" / "dashboard.json"

# Panels that still carry business/SQL logic in rawSql, tracked against a
# follow-up issue (Milestone 2 of Issue #126 converted only the two heaviest
# offenders — panels 79 and 81 — to a BQ pass-through). Remove an entry here
# only after its panel's rawSql has been converted to a pass-through.
WAIVED_PANELS: dict[int, str] = {
    51: "Issue #133: UNION ALL goal-line synthesis computed in-panel",
    52: "Issue #133: correlated subquery joining vw_staff_on_shift",
    72: "Issue #133: JOIN model_forecast_daily to vw_model_labor_daily in-panel",
    75: "Issue #133: JOIN model_forecast_daily to vw_model_labor_daily in-panel",
}

# Panels that MUST be clean (no waiver permitted) — the Order Assistant
# panels this issue specifically fixed. Regressing any back to inline logic
# is a hard failure, not a waivable one. 82 added by Issue #137 (dual-date
# Order Recommendation, Restock 2 — must stay a pure pass-through like 81).
MUST_BE_CLEAN: frozenset[int] = frozenset({79, 81, 82})

# Constructs that indicate real computation, not presentation.
_BANNED_PATTERNS: list[tuple[str, str]] = [
    (r"\bWITH\b", "CTE (WITH ...)"),
    (r"\bUNION\b", "UNION / UNION ALL"),
    (r"\bJOIN\b", "JOIN"),
    (r"\bGENERATE_ARRAY\s*\(", "GENERATE_ARRAY (algorithmic expansion)"),
    (r"\bOVER\s*\(", "window function (OVER (...))"),
    (r"\(\s*SELECT\b", "subquery / correlated SELECT"),
    (r"\bCASE\s+WHEN\b", "CASE WHEN (business-rule branching)"),
]

# A clean presentation-only panel body: SELECT ... FROM `...vw_x`|tvf_x(...)
# optionally followed by WHERE / ORDER BY. This is a coarse structural check
# (not a full SQL parser) — it exists to catch drift, not to validate syntax.
_PASS_THROUGH_FROM = re.compile(
    r"\bFROM\s+`?[\w\-.]*\.(vw_\w+|tvf_\w+)`?\s*(\([^)]*\))?", re.IGNORECASE
)


def _iter_data_panels(dashboard: dict):
    """Yield (panel_id, title, rawSql) for every non-row, non-text panel target."""
    for panel in dashboard.get("panels", []):
        if panel.get("type") in ("row", "text"):
            continue
        for target in panel.get("targets", []) or []:
            sql = target.get("rawSql")
            if sql:
                yield panel.get("id"), panel.get("title", ""), sql


def _violations(sql: str) -> list[str]:
    found = []
    for pattern, label in _BANNED_PATTERNS:
        if re.search(pattern, sql, re.IGNORECASE):
            found.append(label)
    if not _PASS_THROUGH_FROM.search(sql):
        found.append("FROM clause does not read a single vw_*/tvf_* object")
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dashboard", type=pathlib.Path, default=_DEFAULT_DASHBOARD)
    args = ap.parse_args()

    dashboard = json.loads(args.dashboard.read_text())

    hard_failures: list[str] = []
    stale_waivers: list[int] = []

    for panel_id, title, sql in _iter_data_panels(dashboard):
        violations = _violations(sql)
        waived = panel_id in WAIVED_PANELS

        if not violations:
            if waived and panel_id not in MUST_BE_CLEAN:
                stale_waivers.append(panel_id)
            continue

        if panel_id in MUST_BE_CLEAN:
            hard_failures.append(
                f"panel {panel_id} ({title!r}) is in MUST_BE_CLEAN but has logic: "
                f"{', '.join(violations)}"
            )
        elif not waived:
            hard_failures.append(
                f"panel {panel_id} ({title!r}) has logic and no waiver: "
                f"{', '.join(violations)}\n"
                f"  Fix: move the logic into a core/migrations/*.sql view or table "
                f"function, then rewrite the panel as SELECT * FROM <object>.\n"
                f"  Or: add a WAIVED_PANELS entry pointing at a tracked follow-up issue."
            )

    if hard_failures:
        print("check_grafana_no_logic: FAIL\n", file=sys.stderr)
        for f in hard_failures:
            print(f"  - {f}\n", file=sys.stderr)
        return 1

    if stale_waivers:
        print(
            "check_grafana_no_logic: FAIL — these panels are waived but are now "
            f"clean; remove their WAIVED_PANELS entry: {sorted(stale_waivers)}",
            file=sys.stderr,
        )
        return 1

    print(
        f"check_grafana_no_logic: OK — {len(MUST_BE_CLEAN)} Order Assistant panel(s) "
        f"clean, {len(WAIVED_PANELS)} panel(s) waived (tracked follow-up)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
