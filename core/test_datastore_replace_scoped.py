#!/usr/bin/env python3
"""Multi-line natural keys must load exactly and converge on re-run (Issue #338).

Regression for the nightly failures of 2026-09-15, 09-21 and 09-22:

    UPDATE/MERGE must match at most one source row for each target row

ADP's Earnings & Hours statement gave one employee two ``Bonus`` lines on the
2026-09-11 check. ``adp_earnings`` upserted on
(period_start, period_end, employee, description, check_date), so the first load
inserted both into an empty key and every later load was rejected.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import datastore

EARNINGS_KEYS = ["period_start", "period_end", "employee", "description", "check_date"]


def _line(description, amount, *, check_date="2026-09-11", employee="Employee, Test A"):
    return {
        "period_start": "2026-08-24", "period_end": "2026-09-06",
        "check_date": check_date, "employee": employee,
        "description": description, "amount": amount,
    }


class _Job:
    def result(self):
        return []


class _ScopedTableClient:
    """Applies the replace statement's semantics to an in-memory table.

    Only the statements ``replace_rows_scoped`` issues are understood, and the
    MERGE is checked for the ``ON FALSE`` + scoped ``DELETE`` shape before it is
    applied, so a regression to a keyed MERGE fails here rather than in prod.
    """

    def __init__(self, target: list[dict]):
        self.target = target
        self.staged: list[dict] = []
        self.sql: list[str] = []

    def query(self, sql, job_config=None):  # noqa: ARG002
        self.sql.append(sql)
        if sql.startswith("MERGE"):
            assert " ON FALSE " in sql, sql
            m = re.search(r"CAST\(T\.(\w+) AS STRING\) IN \((.*?)\) THEN DELETE", sql)
            assert m, sql
            col, vals = m.group(1), set(re.findall(r"'([^']*)'", m.group(2)))
            self.target = [r for r in self.target if str(r[col]) not in vals] + self.staged
        return _Job()


def _replace(client, rows, scope_col="check_date"):
    def _stage(_client, _fq, _cols, staged, _types):
        client.staged = list(staged)
        return len(staged)

    with mock.patch.object(datastore, "get_client", return_value=client), \
         mock.patch.object(datastore, "_insert_rows", side_effect=_stage), \
         mock.patch.object(datastore, "_assert_sandbox_write_isolation"):
        return datastore.replace_rows_scoped("adp_earnings", rows, scope_col=scope_col)


class TestReplaceRowsScoped(unittest.TestCase):
    def test_two_bonus_lines_survive_a_rerun(self):
        """E1: the exact prod failure — load, then load the same export again."""
        export = [_line("Bonus", 25.0), _line("Bonus", 50.0), _line("Regular", 600.0)]
        client = _ScopedTableClient(target=[])
        _replace(client, export)
        _replace(client, export)
        bonuses = sorted(r["amount"] for r in client.target if r["description"] == "Bonus")
        self.assertEqual(bonuses, [25.0, 50.0])
        self.assertEqual(len(client.target), 3)

    def test_void_and_reissue_triple_is_kept_verbatim(self):
        """E2: April's -x / +x / +x Regular lines all stay, and the sum holds."""
        export = [_line("Regular", -400.0, check_date="2026-04-24"),
                  _line("Regular", 400.0, check_date="2026-04-24"),
                  _line("Regular", 400.0, check_date="2026-04-24")]
        client = _ScopedTableClient(target=list(export))
        _replace(client, export)
        self.assertEqual(len(client.target), 3)
        self.assertAlmostEqual(sum(r["amount"] for r in client.target), 400.0)

    def test_out_of_scope_checks_are_untouched(self):
        older = _line("Regular", 500.0, check_date="2026-08-28")
        client = _ScopedTableClient(target=[older, _line("Bonus", 25.0)])
        _replace(client, [_line("Bonus", 25.0), _line("Bonus", 50.0)])
        self.assertIn(older, client.target)
        self.assertEqual(len(client.target), 3)

    def test_a_line_dropped_by_adp_disappears(self):
        """The export is authoritative for its check: a removed line must not linger."""
        client = _ScopedTableClient(target=[_line("Bonus", 25.0), _line("Bonus", 50.0)])
        _replace(client, [_line("Bonus", 50.0)])
        self.assertEqual([r["amount"] for r in client.target], [50.0])

    def test_null_scope_is_rejected(self):
        """An unscoped row would be re-inserted on every run."""
        client = _ScopedTableClient(target=[])
        with self.assertRaises(ValueError):
            _replace(client, [_line("Bonus", 25.0, check_date=None)])
        self.assertEqual(client.sql, [])


class TestDuplicateMergeKeyGuard(unittest.TestCase):
    def test_differing_rows_on_one_key_raise_before_any_query(self):
        """E3: fail loudly with the table and key rather than reject or overwrite."""
        client = mock.Mock()
        with mock.patch.object(datastore, "table_column_types", return_value={}):
            with self.assertRaises(datastore.DuplicateMergeKeyError) as ctx:
                datastore._merge_rows(
                    client, "`proj.ds.adp_earnings`", list(_line("Bonus", 0).keys()),
                    [_line("Bonus", 25.0), _line("Bonus", 50.0)], EARNINGS_KEYS,
                )
        self.assertIn("proj.ds.adp_earnings", str(ctx.exception))
        self.assertIn("Employee, Test A", str(ctx.exception))
        client.query.assert_not_called()

    def test_exact_duplicates_collapse_to_one(self):
        rows = [_line("Bonus", 25.0), _line("Bonus", 25.0)]
        self.assertEqual(datastore._dedupe_merge_rows("`t`", rows, EARNINGS_KEYS), [rows[0]])

    def test_collision_across_the_200_row_batch_boundary_is_caught(self):
        """The silent-overwrite case: the pair never shares a MERGE statement."""
        filler = [_line("Regular", float(i), employee=f"E{i}") for i in range(250)]
        rows = [_line("Bonus", 25.0)] + filler + [_line("Bonus", 50.0)]
        with self.assertRaises(datastore.DuplicateMergeKeyError):
            datastore._dedupe_merge_rows("`t`", rows, EARNINGS_KEYS)

    def test_distinct_keys_pass_through_in_order(self):
        rows = [_line("Bonus", 25.0), _line("Regular", 600.0)]
        self.assertEqual(datastore._dedupe_merge_rows("`t`", rows, EARNINGS_KEYS), rows)


if __name__ == "__main__":
    unittest.main()
