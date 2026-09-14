#!/usr/bin/env python3
"""Tests for the atomic scoped merge and the natural-key uniqueness guard.

Regression cover for the 2026-09-07 duplication: two concurrent recomputes raced
the DELETE-then-MERGE pair in ``replace_scope`` and each inserted a full copy of
the batch, leaving 389 duplicate keys in ``model_tip_alloc_daily`` across 83 days.
``merge_rows_scoped`` collapses eviction and upsert into one MERGE statement, and
``assert_unique_natural_key`` refuses to let a duplicate survive a write.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import datastore


class _FakeQueryJob:
    def __init__(self, recorder, sql):
        self._recorder = recorder
        self._sql = sql

    def result(self):
        self._recorder.append(self._sql)
        return []


class _FakeClient:
    def __init__(self):
        self.sqls: list[str] = []

    def query(self, sql, job_config=None):  # noqa: ARG002
        return _FakeQueryJob(self.sqls, sql)


ROWS = [
    {"date": "2026-09-01", "employee": "Flores, Juan", "our_share": 12.5},
    {"date": "2026-09-01", "employee": "Krause, Lindsay", "our_share": 10.0},
    {"date": "2026-09-02", "employee": "Flores, Juan", "our_share": 9.25},
]


class TestMergeRowsScoped(unittest.TestCase):
    def _run(self):
        client = _FakeClient()
        with (
            mock.patch.object(datastore, "get_client", return_value=client),
            mock.patch.object(datastore, "_assert_sandbox_write_isolation"),
            mock.patch.object(datastore, "_insert_rows", return_value=len(ROWS)),
            mock.patch.object(datastore, "_DATASET", "bhaga"),
        ):
            n = datastore.merge_rows_scoped(
                "model_tip_alloc_daily",
                ROWS,
                merge_keys=["date", "employee"],
                scope_col="date",
            )
        return n, client.sqls

    def test_returns_row_count(self):
        n, _ = self._run()
        self.assertEqual(n, len(ROWS))

    def test_emits_exactly_one_merge_statement(self):
        _, sqls = self._run()
        merges = [s for s in sqls if s.lstrip().upper().startswith("MERGE")]
        self.assertEqual(len(merges), 1, f"expected 1 MERGE, got {len(merges)}: {sqls}")

    def test_no_standalone_delete(self):
        """The race window was a DELETE in its own job. It must be gone."""
        _, sqls = self._run()
        for s in sqls:
            self.assertFalse(
                s.lstrip().upper().startswith("DELETE"),
                f"standalone DELETE reintroduces the race: {s}",
            )

    def test_merge_evicts_only_within_batch_scope(self):
        _, sqls = self._run()
        merge = next(s for s in sqls if s.lstrip().upper().startswith("MERGE"))
        self.assertIn("WHEN NOT MATCHED BY SOURCE", merge)
        self.assertIn("THEN DELETE", merge)
        # Both batch dates are in scope; an unrelated date must not be.
        self.assertIn("'2026-09-01'", merge)
        self.assertIn("'2026-09-02'", merge)
        self.assertNotIn("'2026-08-15'", merge)

    def test_staging_table_is_dropped(self):
        _, sqls = self._run()
        self.assertTrue(any(s.startswith("CREATE TABLE") for s in sqls))
        self.assertTrue(any(s.startswith("DROP TABLE IF EXISTS") for s in sqls))

    def test_empty_rows_is_a_noop(self):
        with mock.patch.object(datastore, "get_client", return_value=_FakeClient()):
            self.assertEqual(
                datastore.merge_rows_scoped(
                    "model_tip_alloc_daily", [], merge_keys=["date"], scope_col="date"
                ),
                0,
            )


class TestAssertUniqueNaturalKey(unittest.TestCase):
    def test_passes_when_rows_equal_distinct_keys(self):
        with (
            mock.patch.object(datastore, "get_client", return_value=_FakeClient()),
            mock.patch.object(datastore, "read_query", return_value=[{"n": 789, "k": 789}]),
        ):
            datastore.assert_unique_natural_key("model_tip_alloc_daily", ["date", "employee"])

    def test_raises_with_duplicate_count_in_message(self):
        with (
            mock.patch.object(datastore, "get_client", return_value=_FakeClient()),
            mock.patch.object(datastore, "read_query", return_value=[{"n": 1178, "k": 789}]),
            self.assertRaises(RuntimeError) as ctx,
        ):
            datastore.assert_unique_natural_key("model_tip_alloc_daily", ["date", "employee"])
        self.assertIn("389 duplicate row(s)", str(ctx.exception))
        self.assertIn("date, employee", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
