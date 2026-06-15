"""Regression guard: BQ readers return the same shapes as the Sheet readers they replaced.

This test ensures that the *contract* between model_inputs and call sites is
preserved — the only thing that moved is the data source, not the data shape.

Run:
    python3 -m pytest agents/bhaga/scripts/test_model_inputs_regression.py -v
"""
from __future__ import annotations

import datetime
import sys
import os
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))


class TestReadTrainingShiftsShape(unittest.TestCase):
    """read_training_shifts returns the same shape as _read_training_shifts_from_sheet."""

    FIXTURE_ROWS = [
        {"employee_name": "Flores, Juan", "d": "2026-05-18"},
        {"employee_name": "Padron, Lisette", "d": "2026-05-23"},
    ]

    def _sheet_reader_shape(self) -> type:
        """Shape expected by materialize_model_bq: set of (str, str) tuples."""
        return set

    def test_returns_set_of_name_date_tuples(self):
        from agents.bhaga.scripts import model_inputs as mi
        with mock.patch("core.datastore.read_query", return_value=self.FIXTURE_ROWS), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`p.d.{t}`"):
            result = mi.read_training_shifts("palmetto")

        self.assertIsInstance(result, set)
        # Every element must be a 2-tuple of strings.
        for item in result:
            self.assertIsInstance(item, tuple, f"Expected tuple, got {type(item)}: {item!r}")
            self.assertEqual(len(item), 2, f"Expected 2-tuple, got {len(item)}-tuple")
            self.assertIsInstance(item[0], str)
            self.assertIsInstance(item[1], str)

    def test_date_format_is_iso(self):
        from agents.bhaga.scripts import model_inputs as mi
        with mock.patch("core.datastore.read_query", return_value=self.FIXTURE_ROWS), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`p.d.{t}`"):
            result = mi.read_training_shifts("palmetto")

        for name, date_str in result:
            datetime.date.fromisoformat(date_str)  # raises ValueError if format wrong


class TestReadTrainingExcludedShape(unittest.TestCase):
    """read_training_excluded returns same shape as _read_training_excluded_from_sheet."""

    def test_returns_dict_name_to_date(self):
        from agents.bhaga.scripts import model_inputs as mi
        fake_store = {
            "training_excluded:Flores, Juan": "2026-05-16",
            "other": "ignored",
        }
        with mock.patch("core.store_config.get_all", return_value=fake_store):
            result = mi.read_training_excluded("palmetto")

        self.assertIsInstance(result, dict)
        for name, last_date in result.items():
            self.assertIsInstance(name, str)
            self.assertIsInstance(last_date, datetime.date, f"Expected datetime.date for {name!r}")


class TestReadAliasesShape(unittest.TestCase):
    """read_aliases returns same shape as store_profile.load_aliases."""

    FIXTURE_ROWS = [
        {"raw_name": "Juan Flores", "canonical_name": "Flores, Juan"},
        {"raw_name": "Flores, Juan", "canonical_name": "Flores, Juan"},
        {"raw_name": "Lisette Padron", "canonical_name": "Padron, Lisette"},
    ]

    def test_returns_flat_raw_to_canonical_dict(self):
        from agents.bhaga.scripts import model_inputs as mi
        with mock.patch("core.datastore.read_query", return_value=self.FIXTURE_ROWS), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`p.d.{t}`"):
            result = mi.read_aliases("palmetto")

        self.assertIsInstance(result, dict)
        # canonical maps to itself
        self.assertEqual(result.get("Flores, Juan"), "Flores, Juan")
        self.assertEqual(result.get("Juan Flores"), "Flores, Juan")
        for raw, canonical in result.items():
            self.assertIsInstance(raw, str)
            self.assertIsInstance(canonical, str)

    def test_canonical_maps_to_itself(self):
        from agents.bhaga.scripts import model_inputs as mi
        with mock.patch("core.datastore.read_query", return_value=self.FIXTURE_ROWS), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`p.d.{t}`"):
            result = mi.read_aliases("palmetto")

        for canonical in ["Flores, Juan", "Padron, Lisette"]:
            self.assertIn(canonical, result)
            self.assertEqual(result[canonical], canonical)


class TestReadExclusionsShape(unittest.TestCase):
    """read_exclusions returns same shape as store_profile.load_exclusions."""

    def test_returns_dict_with_permanent_and_training_keys(self):
        from agents.bhaga.scripts import model_inputs as mi
        with mock.patch("core.store_config.get_config", return_value="Krause, Lindsay"), \
             mock.patch("core.store_config.get_all", return_value={
                 "training_excluded:Flores, Juan": "2026-05-16",
             }):
            result = mi.read_exclusions("palmetto")

        self.assertIn("permanent", result)
        self.assertIn("training", result)
        self.assertIsInstance(result["permanent"], list)
        self.assertIsInstance(result["training"], dict)

    def test_permanent_is_list_of_strings(self):
        from agents.bhaga.scripts import model_inputs as mi
        with mock.patch("core.store_config.get_config", return_value="A, B;C, D"), \
             mock.patch("core.store_config.get_all", return_value={}):
            result = mi.read_exclusions("palmetto")
        for name in result["permanent"]:
            self.assertIsInstance(name, str)

    def test_training_values_are_strings(self):
        """training values from store_config are strings (date ISO strings), matching Sheet reader output."""
        from agents.bhaga.scripts import model_inputs as mi
        with mock.patch("core.store_config.get_config", return_value=""), \
             mock.patch("core.store_config.get_all", return_value={
                 "training_excluded:Smith, A": "2026-04-01",
             }):
            result = mi.read_exclusions("palmetto")
        for name, val in result["training"].items():
            self.assertIsInstance(name, str)
            self.assertIsInstance(val, str)


if __name__ == "__main__":
    unittest.main()
