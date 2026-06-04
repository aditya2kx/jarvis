#!/usr/bin/env python3
"""Tests for materialize_model_bq's empty-raw guard.

Regression guard for the 2026-06-03 BHAGA BQ incident: when BigQuery raw
`square_transactions` came back empty (the orchestrator SA lacked BQ write
permission, so the mirror was never populated), `materialize` crashed with a
cryptic `max() iterable argument is empty`. It must instead raise a precise,
greppable RuntimeError that names the real cause and the remediation.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

os.environ.setdefault("BHAGA_DATASTORE", "bigquery")

from agents.bhaga.scripts import materialize_model_bq as mm


class MaterializeEmptyGuardTest(unittest.TestCase):
    def test_empty_txns_raises_clear_error(self):
        with mock.patch.object(mm, "read_shifts_bq", return_value=[]), \
             mock.patch.object(mm, "read_transactions_bq", return_value=[]), \
             mock.patch.object(mm, "read_wage_rates_bq", return_value=[]), \
             mock.patch.object(mm, "load_aliases", return_value={}), \
             mock.patch.object(mm, "load_exclusions", return_value={"permanent": []}):
            with self.assertRaises(RuntimeError) as ctx:
                mm.materialize("palmetto", dry_run=True)
        msg = str(ctx.exception)
        self.assertIn("square_transactions", msg)
        self.assertIn("backfill_bigquery", msg)
        # Must NOT be the opaque max()-on-empty failure.
        self.assertNotIn("max() iterable", msg)


if __name__ == "__main__":
    unittest.main()
