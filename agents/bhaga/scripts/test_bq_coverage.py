"""Unit tests for bq_coverage.py.

All tests use unittest.mock.patch to stub core.datastore.read_query so no live
BQ connection is needed.
"""

import datetime
import unittest
from unittest.mock import patch

from agents.bhaga.scripts.bq_coverage import missing_ranges, present_days

_START = datetime.date(2026, 5, 1)
_END = datetime.date(2026, 5, 5)

_TABLE = "square_transactions"
_COL = "date_local"


def _rows(*dates: datetime.date) -> list[dict]:
    """Build the list[dict] that read_query would return for a set of dates."""
    return [{"d": d} for d in dates]


class TestPresentDays(unittest.TestCase):
    def test_returns_dates_from_query(self):
        dates = [datetime.date(2026, 5, 1), datetime.date(2026, 5, 3)]
        with patch("agents.bhaga.scripts.bq_coverage.read_query", return_value=_rows(*dates)) as m:
            result = present_days(_TABLE, _COL, _START, _END)
        self.assertEqual(result, set(dates))
        # Verify the SQL was parameterised correctly
        sql = m.call_args[0][0]
        self.assertIn("2026-05-01", sql)
        self.assertIn("2026-05-05", sql)

    def test_skips_none_values(self):
        """Rows with d=None should be silently dropped."""
        with patch(
            "agents.bhaga.scripts.bq_coverage.read_query",
            return_value=[{"d": None}, {"d": datetime.date(2026, 5, 2)}],
        ):
            result = present_days(_TABLE, _COL, _START, _END)
        self.assertEqual(result, {datetime.date(2026, 5, 2)})

    def test_empty_table(self):
        with patch("agents.bhaga.scripts.bq_coverage.read_query", return_value=[]):
            result = present_days(_TABLE, _COL, _START, _END)
        self.assertEqual(result, set())


class TestMissingRanges(unittest.TestCase):
    def test_full_coverage_returns_empty(self):
        all_days = [_START + datetime.timedelta(i) for i in range((_END - _START).days + 1)]
        with patch("agents.bhaga.scripts.bq_coverage.read_query", return_value=_rows(*all_days)):
            result = missing_ranges(_TABLE, _COL, _START, _END)
        self.assertEqual(result, [])

    def test_all_missing_returns_single_range(self):
        with patch("agents.bhaga.scripts.bq_coverage.read_query", return_value=[]):
            result = missing_ranges(_TABLE, _COL, _START, _END)
        self.assertEqual(result, [(_START, _END)])

    def test_interior_gap_is_isolated(self):
        # Present: 05-01, 05-05  — gap: 05-02..05-04
        present = [datetime.date(2026, 5, 1), datetime.date(2026, 5, 5)]
        with patch("agents.bhaga.scripts.bq_coverage.read_query", return_value=_rows(*present)):
            result = missing_ranges(_TABLE, _COL, _START, _END)
        self.assertEqual(result, [(datetime.date(2026, 5, 2), datetime.date(2026, 5, 4))])

    def test_leading_gap(self):
        # Present: 05-03..05-05  — gap: 05-01..05-02
        present = [datetime.date(2026, 5, 3), datetime.date(2026, 5, 4), datetime.date(2026, 5, 5)]
        with patch("agents.bhaga.scripts.bq_coverage.read_query", return_value=_rows(*present)):
            result = missing_ranges(_TABLE, _COL, _START, _END)
        self.assertEqual(result, [(datetime.date(2026, 5, 1), datetime.date(2026, 5, 2))])

    def test_trailing_gap(self):
        # Present: 05-01..05-03  — gap: 05-04..05-05
        present = [datetime.date(2026, 5, 1), datetime.date(2026, 5, 2), datetime.date(2026, 5, 3)]
        with patch("agents.bhaga.scripts.bq_coverage.read_query", return_value=_rows(*present)):
            result = missing_ranges(_TABLE, _COL, _START, _END)
        self.assertEqual(result, [(datetime.date(2026, 5, 4), datetime.date(2026, 5, 5))])

    def test_multiple_disjoint_gaps(self):
        # Present: 05-01, 05-03, 05-05  — gaps: 05-02 and 05-04
        present = [
            datetime.date(2026, 5, 1),
            datetime.date(2026, 5, 3),
            datetime.date(2026, 5, 5),
        ]
        with patch("agents.bhaga.scripts.bq_coverage.read_query", return_value=_rows(*present)):
            result = missing_ranges(_TABLE, _COL, _START, _END)
        self.assertEqual(result, [
            (datetime.date(2026, 5, 2), datetime.date(2026, 5, 2)),
            (datetime.date(2026, 5, 4), datetime.date(2026, 5, 4)),
        ])

    def test_single_day_window_present(self):
        d = datetime.date(2026, 5, 1)
        with patch("agents.bhaga.scripts.bq_coverage.read_query", return_value=_rows(d)):
            result = missing_ranges(_TABLE, _COL, d, d)
        self.assertEqual(result, [])

    def test_single_day_window_absent(self):
        d = datetime.date(2026, 5, 1)
        with patch("agents.bhaga.scripts.bq_coverage.read_query", return_value=[]):
            result = missing_ranges(_TABLE, _COL, d, d)
        self.assertEqual(result, [(d, d)])


if __name__ == "__main__":
    unittest.main()
