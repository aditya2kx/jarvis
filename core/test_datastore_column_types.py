#!/usr/bin/env python3
"""Column typing must come from the target table, not from guessing.

Regression for the sandbox full-live failure of 2026-09-14:

    Value of type STRING cannot be assigned to T.er_futa, which has type FLOAT64

ADP's Payroll Liability body omitted the FUTA line, so ``er_futa`` was None in
every row of the batch. With nothing to infer from, typing fell back to STRING
and the MERGE was rejected against a FLOAT64 column — a nullable column doing
exactly what nullable means, failing the whole pipeline step.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import datastore


class _Field:
    def __init__(self, name: str, field_type: str):
        self.name = name
        self.field_type = field_type


class _FakeTable:
    def __init__(self, schema):
        self.schema = schema


class _FakeClient:
    def __init__(self, schema=None, fail: bool = False):
        self._schema = schema or []
        self._fail = fail
        self.get_table_calls: list[str] = []

    def get_table(self, key):
        self.get_table_calls.append(key)
        if self._fail:
            raise RuntimeError("no such table")
        return _FakeTable(self._schema)


LIABILITY_SCHEMA = [
    _Field("check_date", "DATE"),
    _Field("payroll_label", "STRING"),
    _Field("er_futa", "FLOAT64"),
    _Field("is_final", "BOOLEAN"),
]


class TestTableColumnTypes(unittest.TestCase):
    def setUp(self):
        datastore._SCHEMA_TYPE_CACHE.clear()

    def test_reads_types_from_the_table(self):
        client = _FakeClient(LIABILITY_SCHEMA)
        types = datastore.table_column_types(client, "`proj.ds.adp_payroll_liability`")
        self.assertEqual(types["er_futa"], "FLOAT64")
        self.assertEqual(types["check_date"], "DATE")

    def test_boolean_is_normalised_to_the_parameter_spelling(self):
        """BQ schemas say BOOLEAN; query parameters must say BOOL."""
        client = _FakeClient(LIABILITY_SCHEMA)
        types = datastore.table_column_types(client, "`proj.ds.adp_payroll_liability`")
        self.assertEqual(types["is_final"], "BOOL")

    def test_backticks_are_stripped_before_lookup(self):
        client = _FakeClient(LIABILITY_SCHEMA)
        datastore.table_column_types(client, "`proj.ds.t`")
        self.assertEqual(client.get_table_calls, ["proj.ds.t"])

    def test_schema_is_fetched_once_per_table(self):
        client = _FakeClient(LIABILITY_SCHEMA)
        for _ in range(5):
            datastore.table_column_types(client, "`proj.ds.t`")
        self.assertEqual(len(client.get_table_calls), 1)

    def test_unreadable_schema_degrades_instead_of_raising(self):
        """Typing is best-effort — a failed lookup must not fail the write."""
        client = _FakeClient(fail=True)
        self.assertEqual(datastore.table_column_types(client, "`proj.ds.t`"), {})


class TestResolveColTypes(unittest.TestCase):
    COLUMNS = ["check_date", "er_futa", "note"]

    def _resolve(self, batch, hints=None, schema=None):
        return datastore._resolve_col_types(
            self.COLUMNS, batch, hints or {}, schema or {},
        )

    def test_all_none_column_takes_the_schema_type(self):
        """The bug: nothing to infer from, and STRING is the wrong answer."""
        batch = [{"check_date": "2026-09-12", "er_futa": None, "note": None}]
        types = self._resolve(batch, schema={"er_futa": "FLOAT64"})
        self.assertEqual(types["er_futa"], "FLOAT64")

    def test_explicit_hint_still_outranks_the_schema(self):
        batch = [{"er_futa": None}]
        types = self._resolve(batch, hints={"er_futa": "NUMERIC"},
                              schema={"er_futa": "FLOAT64"})
        self.assertEqual(types["er_futa"], "NUMERIC")

    def test_column_absent_from_schema_falls_back_to_inference(self):
        batch = [{"check_date": None, "er_futa": None, "note": "hi"}]
        types = self._resolve(batch, schema={"er_futa": "FLOAT64"})
        self.assertEqual(types["note"], "STRING")

    def test_string_remains_the_last_resort(self):
        batch = [{"check_date": None, "er_futa": None, "note": None}]
        self.assertEqual(self._resolve(batch)["er_futa"], "STRING")

    def test_schema_wins_over_a_misleading_python_value(self):
        """An int in a FLOAT64 column must not be typed INT64 for the parameter."""
        batch = [{"er_futa": 40}]
        types = self._resolve(batch, schema={"er_futa": "FLOAT64"})
        self.assertEqual(types["er_futa"], "FLOAT64")


class TestMergeUsesSchemaTypes(unittest.TestCase):
    """End-to-end through _merge_rows: the emitted SQL must cast NULL correctly."""

    def setUp(self):
        datastore._SCHEMA_TYPE_CACHE.clear()

    def test_null_is_cast_to_the_column_type(self):
        sqls = []

        class _Job:
            def __init__(self, sql):
                self.sql = sql

            def result(self):
                sqls.append(self.sql)
                return []

        client = _FakeClient(LIABILITY_SCHEMA)
        client.query = lambda sql, job_config=None: _Job(sql)  # noqa: ARG005

        rows = [{"check_date": "2026-09-12", "payroll_label": "x", "er_futa": None}]
        with mock.patch.object(datastore, "_PROJECT_ID", "proj"), \
             mock.patch.object(datastore, "_DATASET", "ds"):
            datastore._merge_rows(
                client, "`proj.ds.adp_payroll_liability`",
                ["check_date", "payroll_label", "er_futa"], rows,
                ["check_date", "payroll_label"],
            )

        merged = "\n".join(sqls)
        self.assertIn("CAST(NULL AS FLOAT64) AS er_futa", merged)
        self.assertNotIn("CAST(NULL AS STRING) AS er_futa", merged)


if __name__ == "__main__":
    unittest.main()
