"""Tests for agents.bhaga.scripts.status — BHAGA pipeline freshness checker.

Two groups:

1. Behavior tests (all network mocked) — verify green/red paths, exit codes,
   mode-specific query logic (iso_week, period_coverage), and the default date.

2. Anti-drift sync tests (file parsing, no mocks, no network) — parse the
   dashboard JSON and migration SQL to assert the declarative registry in
   status.py stays in sync with schema and Grafana panels.  These are the
   tests that fail CI when a migration or dashboard change lands without
   updating the doctor.
"""

from __future__ import annotations

import datetime
import importlib
import json
import os
import pathlib
import re
import sys
import zoneinfo

import pytest

# Ensure repo root is importable.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

# status.py runs os.environ.setdefault("BHAGA_DATASTORE", "bigquery") at
# import time.  That is fine for tests — we mock read_query so no actual BQ
# calls happen.
import agents.bhaga.scripts.status as status

# ── Fixtures / helpers ────────────────────────────────────────────────────────

_FIXED_DATE = datetime.date(2026, 6, 3)
_FIXED_ISO = "2026-06-03"

_FAKE_PROFILE = {
    "google_sheets": {
        "bhaga_model": {"spreadsheet_id": "FAKE_MODEL_SID"},
    }
}

# Minimal Sheet rows: header + one data row for the target date.
def _make_sheet_config_rows(dwe: str = _FIXED_ISO) -> list[list]:
    return [
        ["key", "value", "notes"],
        ["data_window_end", dwe, ""],
        ["saturation_orders_per_labor_hour", "3.5", ""],
    ]


def _make_sheet_data_rows(date: str = _FIXED_ISO) -> list[list]:
    return [
        ["date", "gross_sales", "tip_pool"],
        [date, "1200.00", "180.00"],
    ]


# Canned BQ row for a single COUNT(*)/MAX query.
def _bq_row(count: int = 1, max_d: str | None = _FIXED_ISO) -> list[dict]:
    return [{"c": count, "m": max_d}]


def _bq_row_empty() -> list[dict]:
    return [{"c": 0, "m": None}]


def _make_full_bq_responses(date: datetime.date = _FIXED_DATE) -> dict[str, list[dict]]:
    """Build a table-keyed dict of canned BQ responses (all present)."""
    week = status._iso_week(date)
    responses: dict[str, list[dict]] = {}
    for t in status.BQ_TARGETS + status.GRAFANA_VIEWS:
        key = f"{t.table}:{t.mode}"
        if t.mode == "iso_week":
            responses[key] = [{"c": 1, "m": week}]
        elif t.mode == "period_coverage":
            responses[key] = [{"c": 1, "m": "2026-05-18"}]
        else:
            responses[key] = _bq_row()
    return responses


def _mock_read_query(responses: dict[str, list[dict]]):
    """Return a read_query mock that dispatches by (table, mode) key."""
    all_targets = status.BQ_TARGETS + status.GRAFANA_VIEWS

    def _fake_read_query(sql: str) -> list[dict]:
        for t in all_targets:
            if f".{t.table}`" in sql or f".{t.table} " in sql:
                key = f"{t.table}:{t.mode}"
                return responses.get(key, _bq_row())
        return []

    return _fake_read_query


def _mock_read_tab(tab_data: dict[str, list[list]]):
    """Return a _read_tab mock that returns rows keyed by tab name."""
    def _fake(spreadsheet_id: str, tab: str, token: str) -> list[list]:
        return tab_data.get(tab, [])
    return _fake


# ── Behavior tests ────────────────────────────────────────────────────────────


class TestMainGreenPath:
    """All layers present → exit 0."""

    def test_returns_zero_when_all_present(self, monkeypatch):
        tab_data = {
            "config": _make_sheet_config_rows(),
            "daily": _make_sheet_data_rows(),
            "tip_alloc_daily": _make_sheet_data_rows(),
        }
        bq_responses = _make_full_bq_responses(_FIXED_DATE)

        monkeypatch.setattr(status, "refresh_access_token", lambda account=None: "tok")
        monkeypatch.setattr(status, "resolve_sheet_id", lambda k, p: "FAKE_MODEL_SID")
        monkeypatch.setattr(status, "_read_tab", _mock_read_tab(tab_data))
        monkeypatch.setattr(status, "read_query", _mock_read_query(bq_responses))
        monkeypatch.setattr(
            pathlib.Path, "read_text",
            lambda self, **kw: json.dumps(_FAKE_PROFILE),
        )

        rc = status.main(["--store", "palmetto", "--date", _FIXED_ISO])
        assert rc == 0

    def test_json_output_ok_verdict(self, monkeypatch, capsys):
        tab_data = {
            "config": _make_sheet_config_rows(),
            "daily": _make_sheet_data_rows(),
            "tip_alloc_daily": _make_sheet_data_rows(),
        }
        bq_responses = _make_full_bq_responses(_FIXED_DATE)

        monkeypatch.setattr(status, "refresh_access_token", lambda account=None: "tok")
        monkeypatch.setattr(status, "resolve_sheet_id", lambda k, p: "FAKE_MODEL_SID")
        monkeypatch.setattr(status, "_read_tab", _mock_read_tab(tab_data))
        monkeypatch.setattr(status, "read_query", _mock_read_query(bq_responses))
        monkeypatch.setattr(
            pathlib.Path, "read_text",
            lambda self, **kw: json.dumps(_FAKE_PROFILE),
        )

        rc = status.main(["--store", "palmetto", "--date", _FIXED_ISO, "--json"])
        assert rc == 0
        captured = json.loads(capsys.readouterr().out)
        assert captured["verdict"] == "OK"
        assert captured["date"] == _FIXED_ISO
        assert "grafana_dashboard" in captured
        assert all(r["present"] for r in captured["results"])


class TestMissingLayerReturnsOne:
    """Any missing layer → exit 1."""

    def _base_tab_data(self) -> dict[str, list[list]]:
        return {
            "config": _make_sheet_config_rows(),
            "daily": _make_sheet_data_rows(),
            "tip_alloc_daily": _make_sheet_data_rows(),
        }

    def _run(self, monkeypatch, bq_responses: dict) -> int:
        monkeypatch.setattr(status, "refresh_access_token", lambda account=None: "tok")
        monkeypatch.setattr(status, "resolve_sheet_id", lambda k, p: "FAKE_MODEL_SID")
        monkeypatch.setattr(status, "_read_tab", _mock_read_tab(self._base_tab_data()))
        monkeypatch.setattr(status, "read_query", _mock_read_query(bq_responses))
        monkeypatch.setattr(
            pathlib.Path, "read_text",
            lambda self, **kw: json.dumps(_FAKE_PROFILE),
        )
        return status.main(["--store", "palmetto", "--date", _FIXED_ISO])

    def test_missing_bq_model_daily(self, monkeypatch):
        bq = _make_full_bq_responses()
        bq["model_daily:exact"] = _bq_row_empty()
        assert self._run(monkeypatch, bq) == 1

    def test_missing_raw_square_transactions(self, monkeypatch):
        bq = _make_full_bq_responses()
        bq["square_transactions:exact"] = _bq_row_empty()
        assert self._run(monkeypatch, bq) == 1

    def test_missing_grafana_view(self, monkeypatch):
        bq = _make_full_bq_responses()
        bq["vw_daily_sales:exact"] = _bq_row_empty()
        assert self._run(monkeypatch, bq) == 1

    def test_sheets_date_mismatch(self, monkeypatch):
        tab_data = {
            "config": _make_sheet_config_rows(dwe="2026-06-02"),  # one day behind
            "daily": _make_sheet_data_rows(),
            "tip_alloc_daily": _make_sheet_data_rows(),
        }
        bq = _make_full_bq_responses()
        monkeypatch.setattr(status, "refresh_access_token", lambda account=None: "tok")
        monkeypatch.setattr(status, "resolve_sheet_id", lambda k, p: "FAKE_MODEL_SID")
        monkeypatch.setattr(status, "_read_tab", _mock_read_tab(tab_data))
        monkeypatch.setattr(status, "read_query", _mock_read_query(bq))
        monkeypatch.setattr(
            pathlib.Path, "read_text",
            lambda self, **kw: json.dumps(_FAKE_PROFILE),
        )
        rc = status.main(["--store", "palmetto", "--date", _FIXED_ISO])
        assert rc == 1

    def test_json_verdict_missing(self, monkeypatch, capsys):
        bq = _make_full_bq_responses()
        bq["model_daily:exact"] = _bq_row_empty()

        monkeypatch.setattr(status, "refresh_access_token", lambda account=None: "tok")
        monkeypatch.setattr(status, "resolve_sheet_id", lambda k, p: "FAKE_MODEL_SID")
        monkeypatch.setattr(status, "_read_tab", _mock_read_tab(self._base_tab_data()))
        monkeypatch.setattr(status, "read_query", _mock_read_query(bq))
        monkeypatch.setattr(
            pathlib.Path, "read_text",
            lambda self, **kw: json.dumps(_FAKE_PROFILE),
        )
        rc = status.main(["--store", "palmetto", "--date", _FIXED_ISO, "--json"])
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        assert out["verdict"] == "MISSING"


class TestIsoWeekMode:
    """model_labor_weekly uses ISO week matching, not a literal date."""

    def test_iso_week_key_used(self, monkeypatch):
        """Read_query for model_labor_weekly must receive the ISO week string."""
        captured_sql: list[str] = []

        def _spy(sql: str) -> list[dict]:
            captured_sql.append(sql)
            return _bq_row()

        tab_data = {
            "config": _make_sheet_config_rows(),
            "daily": _make_sheet_data_rows(),
            "tip_alloc_daily": _make_sheet_data_rows(),
        }
        monkeypatch.setattr(status, "refresh_access_token", lambda account=None: "tok")
        monkeypatch.setattr(status, "resolve_sheet_id", lambda k, p: "sid")
        monkeypatch.setattr(status, "_read_tab", _mock_read_tab(tab_data))
        monkeypatch.setattr(status, "read_query", _spy)
        monkeypatch.setattr(
            pathlib.Path, "read_text",
            lambda self, **kw: json.dumps(_FAKE_PROFILE),
        )

        status.main(["--store", "palmetto", "--date", _FIXED_ISO])

        week_sql = [s for s in captured_sql if "model_labor_weekly" in s]
        assert week_sql, "no SQL emitted for model_labor_weekly"
        expected_week = status._iso_week(_FIXED_DATE)
        assert expected_week in week_sql[0], (
            f"Expected ISO week {expected_week!r} in model_labor_weekly SQL; "
            f"got: {week_sql[0]!r}"
        )
        # Must NOT contain the literal date
        assert _FIXED_ISO not in week_sql[0]

    def test_iso_week_helper(self):
        # 2026-01-01 is Thursday → week 1 starts Mon 2025-12-29.
        # 2026-01-05 is Monday → first day of week 2.
        assert status._iso_week(datetime.date(2026, 1, 5)) == "2026-W02"
        # 2026-01-04 (Sunday) is in week 1 of 2026 (week containing Jan 1 Thu).
        assert status._iso_week(datetime.date(2026, 1, 4)) == "2026-W01"
        assert status._iso_week(datetime.date(2026, 6, 3)) == "2026-W23"
        # A known 53-week year: 2020 had week 53 (Dec 31 was a Thursday).
        assert status._iso_week(datetime.date(2020, 12, 28)) == "2020-W53"


class TestPeriodCoverageMode:
    """model_period_summary and vw_model_period_summary use period coverage query."""

    def test_period_coverage_sql_uses_range(self, monkeypatch):
        captured: list[str] = []

        def _spy(sql: str) -> list[dict]:
            captured.append(sql)
            return _bq_row()

        tab_data = {
            "config": _make_sheet_config_rows(),
            "daily": _make_sheet_data_rows(),
            "tip_alloc_daily": _make_sheet_data_rows(),
        }
        monkeypatch.setattr(status, "refresh_access_token", lambda account=None: "tok")
        monkeypatch.setattr(status, "resolve_sheet_id", lambda k, p: "sid")
        monkeypatch.setattr(status, "_read_tab", _mock_read_tab(tab_data))
        monkeypatch.setattr(status, "read_query", _spy)
        monkeypatch.setattr(
            pathlib.Path, "read_text",
            lambda self, **kw: json.dumps(_FAKE_PROFILE),
        )

        status.main(["--store", "palmetto", "--date", _FIXED_ISO])

        period_sqls = [s for s in captured if "model_period_summary" in s]
        assert period_sqls, "no SQL emitted for model_period_summary"
        sql = period_sqls[0]
        assert "period_start <=" in sql
        assert "period_end >=" in sql
        # The literal date must be used for the range bounds
        assert _FIXED_ISO in sql


class TestDefaultDateYesterdayCT:
    """--date omitted → yesterday in America/Chicago."""

    def test_default_date_is_yesterday_ct(self, monkeypatch):
        tz = zoneinfo.ZoneInfo("America/Chicago")
        fixed_now = datetime.datetime(2026, 6, 4, 8, 0, tzinfo=tz)
        expected_yesterday = datetime.date(2026, 6, 3)

        captured_dates: list[datetime.date] = []

        real_run_bq = status._run_bq_target

        def _spy(t, date, layer="bq"):
            captured_dates.append(date)
            return status.CheckResult(layer, t.table, True, 1, date.isoformat())

        tab_data = {
            "config": _make_sheet_config_rows(dwe="2026-06-03"),
            "daily": _make_sheet_data_rows(),
            "tip_alloc_daily": _make_sheet_data_rows(),
        }
        monkeypatch.setattr(status, "refresh_access_token", lambda account=None: "tok")
        monkeypatch.setattr(status, "resolve_sheet_id", lambda k, p: "sid")
        monkeypatch.setattr(status, "_read_tab", _mock_read_tab(tab_data))
        monkeypatch.setattr(status, "_run_bq_target", _spy)
        monkeypatch.setattr(
            pathlib.Path, "read_text",
            lambda self, **kw: json.dumps(_FAKE_PROFILE),
        )

        import unittest.mock as mock
        with mock.patch("agents.bhaga.scripts.status._yesterday_chicago",
                        return_value=expected_yesterday):
            status.main(["--store", "palmetto"])

        assert expected_yesterday in captured_dates


# ── Anti-drift sync tests ─────────────────────────────────────────────────────
#
# These tests parse the dashboard JSON and migration SQL files at test time to
# assert that status.py's registries stay in sync.  They contain no mocks and
# make no network calls.  They are the hard CI gate for schema / panel drift.

_MIGRATIONS_DIR = _REPO_ROOT / "core" / "migrations"
_DASHBOARD_JSON = _REPO_ROOT / "agents" / "bhaga" / "grafana" / "dashboard.json"


class TestGrafanaContractInSync:
    """Every vw_* view referenced in dashboard.json must be in GRAFANA_VIEWS."""

    def _vw_refs_from_dashboard(self) -> set[str]:
        """Extract all `bhaga.<name>` table/view references from panel rawSql."""
        raw = json.loads(_DASHBOARD_JSON.read_text())
        found: set[str] = set()
        for panel in raw.get("panels", []):
            for tgt in panel.get("targets", []):
                sql = tgt.get("rawSql", "")
                # Match `jarvis-bhaga-prod.bhaga.<name>` or `bhaga.<name>`
                for m in re.finditer(
                    r"(?:jarvis-bhaga-prod\.bhaga\.|`jarvis-bhaga-prod`\.`bhaga`\.)`?(\w+)`?",
                    sql,
                ):
                    found.add(m.group(1))
        return found

    def test_all_dashboard_vw_views_are_in_registry(self):
        """Adding a Grafana panel on a new vw_* view must update GRAFANA_VIEWS."""
        dashboard_refs = self._vw_refs_from_dashboard()
        vw_refs = {r for r in dashboard_refs if r.startswith("vw_")}
        registry_views = {t.table for t in status.GRAFANA_VIEWS}

        uncovered = vw_refs - registry_views
        assert not uncovered, (
            f"These vw_* views appear in dashboard.json but are NOT in "
            f"status.GRAFANA_VIEWS: {sorted(uncovered)}\n"
            f"Add them to GRAFANA_VIEWS in agents/bhaga/scripts/status.py."
        )

    def test_non_vw_dashboard_refs_are_in_allowlist(self):
        """Non-vw_* table refs from the dashboard must be in KNOWN_UNCHECKED_GRAFANA_REFS."""
        dashboard_refs = self._vw_refs_from_dashboard()
        non_vw = {r for r in dashboard_refs if not r.startswith("vw_")}
        allowlist = status.KNOWN_UNCHECKED_GRAFANA_REFS

        uncovered = non_vw - allowlist
        assert not uncovered, (
            f"These non-vw_* tables appear in dashboard.json but are NOT in "
            f"status.KNOWN_UNCHECKED_GRAFANA_REFS: {sorted(uncovered)}\n"
            f"Either add them to GRAFANA_VIEWS (if they are views) or add them "
            f"to KNOWN_UNCHECKED_GRAFANA_REFS with an explanatory comment."
        )


class TestModelTablesInSync:
    """Every model_* table in 003_model_tables.sql must be in BQ_TARGETS."""

    def _model_tables_from_sql(self) -> set[str]:
        sql = (_MIGRATIONS_DIR / "003_model_tables.sql").read_text()
        # Match: CREATE TABLE IF NOT EXISTS `...bhaga.model_foo` or bhaga.model_foo
        found: set[str] = set()
        for m in re.finditer(
            r"CREATE\s+(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(?:\w+\.)*?(model_\w+)`?",
            sql,
            re.IGNORECASE,
        ):
            found.add(m.group(1))
        return found

    def test_all_model_tables_covered_by_bq_targets(self):
        """Adding a new model_* table/view must update BQ_TARGETS."""
        sql_tables = self._model_tables_from_sql()
        registry_tables = {t.table for t in status.BQ_TARGETS}
        # GRAFANA_VIEWS can also cover vw_model_* views
        registry_tables |= {t.table for t in status.GRAFANA_VIEWS}

        uncovered = sql_tables - registry_tables
        assert not uncovered, (
            f"These model_* tables/views appear in 003_model_tables.sql but are "
            f"NOT in status.BQ_TARGETS or status.GRAFANA_VIEWS: {sorted(uncovered)}\n"
            f"Add them to the appropriate registry in agents/bhaga/scripts/status.py."
        )


class TestDateColumnsExistInSchemaSql:
    """Each Target's date_column must appear in the SQL definition for that table/view."""

    def _load_all_sql(self) -> str:
        """Concatenate all migration SQL files."""
        parts = []
        for f in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            parts.append(f.read_text())
        return "\n".join(parts)

    def _extract_block(self, sql: str, name: str) -> str:
        """Extract the SQL block that defines table/view `name`."""
        # Match CREATE TABLE/VIEW ... name ... (optionally backtick-quoted)
        pattern = re.compile(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
            r"[`'\"]?(?:[\w\-]+\.)*" + re.escape(name) + r"[`'\"]?"
            r"[\s\S]*?(?=CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)|$)",
            re.IGNORECASE,
        )
        m = pattern.search(sql)
        return m.group(0) if m else ""

    def test_bq_target_date_columns_exist(self):
        sql = self._load_all_sql()
        missing: list[str] = []
        for t in status.BQ_TARGETS:
            block = self._extract_block(sql, t.table)
            if not block:
                # Table may be defined outside migrations (e.g. created at runtime);
                # skip if no SQL block found.
                continue
            # Column appears either as a standalone word or quoted in the block.
            if not re.search(r"\b" + re.escape(t.date_column) + r"\b", block):
                missing.append(f"{t.table}.{t.date_column}")

        assert not missing, (
            f"These BQ_TARGETS date_columns were NOT found in the migration SQL "
            f"definition for their table: {missing}\n"
            f"Update the Target registry in agents/bhaga/scripts/status.py "
            f"to use the current column name."
        )

    def test_grafana_view_date_columns_exist(self):
        sql = self._load_all_sql()
        missing: list[str] = []
        for t in status.GRAFANA_VIEWS:
            block = self._extract_block(sql, t.table)
            if not block:
                continue
            if not re.search(r"\b" + re.escape(t.date_column) + r"\b", block):
                missing.append(f"{t.table}.{t.date_column}")

        assert not missing, (
            f"These GRAFANA_VIEWS date_columns were NOT found in the migration SQL "
            f"definition for their view: {missing}\n"
            f"Update the Target registry in agents/bhaga/scripts/status.py "
            f"to use the current column name."
        )
