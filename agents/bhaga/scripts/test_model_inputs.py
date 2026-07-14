"""Unit tests for agents.bhaga.scripts.model_inputs.

Tests verify that the BQ readers return the same shapes as the legacy
Sheet readers they replace, and degrade gracefully on empty results.

Run:
    python3 -m pytest agents/bhaga/scripts/test_model_inputs.py -v
"""
from __future__ import annotations

import datetime
import sys
import os
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

import agents.bhaga.scripts.model_inputs as mi


class TestReadTrainingShifts(unittest.TestCase):
    def test_returns_dict_of_meta(self):
        fake_rows = [
            {
                "employee_name": "Flores, Juan", "d": "2026-06-01",
                "exempt_start": None, "exempt_end": None, "note": "training",
            },
            {
                "employee_name": "Smith, Alice", "d": "2026-05-28",
                "exempt_start": "18:00", "exempt_end": "18:30", "note": "Meeting",
            },
        ]
        with mock.patch("core.datastore.read_query", return_value=fake_rows), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`proj.ds.{t}`"):
            result = mi.read_training_shifts("palmetto")
        self.assertIsInstance(result, dict)
        self.assertIn(("Flores, Juan", "2026-06-01"), result)
        self.assertIn(("Smith, Alice", "2026-05-28"), result)
        self.assertEqual(result[("Smith, Alice", "2026-05-28")]["exempt_end"], "18:30")

    def test_empty_when_no_rows(self):
        with mock.patch("core.datastore.read_query", return_value=[]), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`proj.ds.{t}`"):
            result = mi.read_training_shifts()
        self.assertEqual(result, {})

    def test_skips_rows_with_missing_name(self):
        fake_rows = [
            {"employee_name": "", "d": "2026-06-01",
             "exempt_start": None, "exempt_end": None, "note": None},
            {"employee_name": "Flores, Juan", "d": "2026-06-02",
             "exempt_start": None, "exempt_end": None, "note": None},
        ]
        with mock.patch("core.datastore.read_query", return_value=fake_rows), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`proj.ds.{t}`"):
            result = mi.read_training_shifts()
        self.assertEqual(set(result.keys()), {("Flores, Juan", "2026-06-02")})

    def test_degrades_on_exception(self):
        with mock.patch("core.datastore.read_query", side_effect=Exception("BQ down")), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`proj.ds.{t}`"):
            result = mi.read_training_shifts()
        self.assertEqual(result, {})


class TestReadTrainingExcluded(unittest.TestCase):
    def test_returns_dict_of_dates(self):
        fake_store = {
            "training_excluded: Flores, Juan": "2026-05-16",
            "training_excluded: Smith, Alice": "2026-04-30",
            "some_other_key": "ignored",
        }
        with mock.patch("core.store_config.get_all", return_value=fake_store):
            result = mi.read_training_excluded("palmetto")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["Flores, Juan"], datetime.date(2026, 5, 16))
        self.assertEqual(result["Smith, Alice"], datetime.date(2026, 4, 30))
        self.assertNotIn("some_other_key", result)

    def test_ignores_empty_values(self):
        fake_store = {"training_excluded:Doe, Jane": ""}
        with mock.patch("core.store_config.get_all", return_value=fake_store):
            result = mi.read_training_excluded()
        self.assertEqual(result, {})

    def test_skips_unparseable_date(self):
        fake_store = {"training_excluded:Doe, Jane": "not-a-date"}
        with mock.patch("core.store_config.get_all", return_value=fake_store):
            result = mi.read_training_excluded()
        self.assertEqual(result, {})

    def test_degrades_on_exception(self):
        with mock.patch("core.store_config.get_all", side_effect=Exception("BQ down")):
            result = mi.read_training_excluded()
        self.assertEqual(result, {})


class TestReadAliases(unittest.TestCase):
    def test_returns_raw_and_canonical_map(self):
        fake_rows = [
            {"raw_name": "Juan Flores", "canonical_name": "Flores, Juan"},
            {"raw_name": "Flores, Juan", "canonical_name": "Flores, Juan"},
        ]
        with mock.patch("core.datastore.read_query", return_value=fake_rows), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`proj.ds.{t}`"):
            result = mi.read_aliases()
        self.assertEqual(result["Juan Flores"], "Flores, Juan")
        self.assertEqual(result["Flores, Juan"], "Flores, Juan")

    def test_canonical_maps_to_itself(self):
        fake_rows = [
            {"raw_name": "AliasName", "canonical_name": "Real, Name"},
        ]
        with mock.patch("core.datastore.read_query", return_value=fake_rows), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`proj.ds.{t}`"):
            result = mi.read_aliases()
        self.assertIn("Real, Name", result)
        self.assertEqual(result["Real, Name"], "Real, Name")

    def test_empty_on_no_rows(self):
        with mock.patch("core.datastore.read_query", return_value=[]), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`proj.ds.{t}`"):
            result = mi.read_aliases()
        self.assertEqual(result, {})

    def test_degrades_on_exception(self):
        with mock.patch("core.datastore.read_query", side_effect=Exception("BQ down")), \
             mock.patch("core.datastore.fq", side_effect=lambda t: f"`proj.ds.{t}`"):
            result = mi.read_aliases()
        self.assertEqual(result, {})


class TestReadExclusions(unittest.TestCase):
    def test_returns_permanent_and_training(self):
        fake_store = {
            "excluded_from_tip_pool": "Krause, Lindsay;Doe, Jane",
            "training_excluded:Flores, Juan": "2026-05-16",
        }
        with mock.patch("core.store_config.get_config", return_value="Krause, Lindsay;Doe, Jane"), \
             mock.patch("core.store_config.get_all", return_value=fake_store):
            result = mi.read_exclusions()
        self.assertIsInstance(result, dict)
        self.assertIn("permanent", result)
        self.assertIn("training", result)
        self.assertIn("Krause, Lindsay", result["permanent"])
        self.assertIn("Doe, Jane", result["permanent"])
        self.assertIn("Flores, Juan", result["training"])

    def test_single_permanent_no_semicolon(self):
        with mock.patch("core.store_config.get_config", return_value="Krause, Lindsay"), \
             mock.patch("core.store_config.get_all", return_value={}):
            result = mi.read_exclusions()
        self.assertEqual(result["permanent"], ["Krause, Lindsay"])

    def test_empty_training_when_no_keys(self):
        with mock.patch("core.store_config.get_config", return_value="Krause, Lindsay"), \
             mock.patch("core.store_config.get_all", return_value={}):
            result = mi.read_exclusions()
        self.assertEqual(result["training"], {})

    def test_degrades_on_exception(self):
        with mock.patch("core.store_config.get_config", side_effect=Exception("BQ down")):
            result = mi.read_exclusions()
        self.assertEqual(result, {"permanent": [], "training": {}})


class TestNormalizeInputName(unittest.TestCase):
    """normalize_input_name resolves typo'd names and raises loudly on truly unknown names."""

    def _aliases(self):
        return {
            "Willingham, Brooke": "Willingham, Brooke",  # canonical maps to itself
            "Wilingham, Brooke": "Willingham, Brooke",   # typo alias
            "Flores, Juan": "Flores, Juan",
        }

    def test_canonical_resolves_to_itself(self):
        with mock.patch.object(mi, "read_aliases", return_value=self._aliases()):
            result = mi.normalize_input_name("palmetto", "Flores, Juan")
        self.assertEqual(result, "Flores, Juan")

    def test_typo_resolves_to_canonical(self):
        """Wilingham (one L) must resolve to Willingham (two Ls) via alias."""
        with mock.patch.object(mi, "read_aliases", return_value=self._aliases()):
            result = mi.normalize_input_name("palmetto", "Wilingham, Brooke")
        self.assertEqual(result, "Willingham, Brooke")

    def test_strips_whitespace(self):
        with mock.patch.object(mi, "read_aliases", return_value=self._aliases()):
            result = mi.normalize_input_name("palmetto", "  Flores, Juan  ")
        self.assertEqual(result, "Flores, Juan")

    def test_unknown_name_raises_valueerror(self):
        """A genuinely unknown name must raise ValueError, not silently no-op."""
        with mock.patch.object(mi, "read_aliases", return_value=self._aliases()):
            with self.assertRaises(ValueError) as ctx:
                mi.normalize_input_name("palmetto", "Zzz, Unknown")
        self.assertIn("Zzz, Unknown", str(ctx.exception))
        self.assertIn("employee_aliases", str(ctx.exception))

    def test_unknown_name_mentions_alias_set_command(self):
        with mock.patch.object(mi, "read_aliases", return_value=self._aliases()):
            with self.assertRaises(ValueError) as ctx:
                mi.normalize_input_name("palmetto", "Nobody")
        self.assertIn("/bhaga-cloud alias set", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
