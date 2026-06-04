"""Tests for render_model_sheet_from_bq — cell rendering and tab spec."""
from __future__ import annotations

import datetime
import sys
import os
import unittest


def _load():
    os.environ.setdefault("BHAGA_DATASTORE", "disabled")
    import agents.bhaga.scripts.render_model_sheet_from_bq as m
    return m


class TestRenderCell(unittest.TestCase):

    def setUp(self):
        self.m = _load()

    def test_none_becomes_empty_string(self):
        self.assertEqual(self.m._render_cell("orders", None), "")

    def test_date_gets_apostrophe_prefix(self):
        val = datetime.date(2026, 4, 7)
        rendered = self.m._render_cell("date", val)
        self.assertTrue(str(rendered).startswith("'"), f"Expected apostrophe prefix, got {rendered!r}")
        self.assertIn("2026-04-07", str(rendered))

    def test_is_open_true_renders_yes(self):
        self.assertEqual(self.m._render_cell("is_open", True), "yes")

    def test_is_open_false_renders_no(self):
        self.assertEqual(self.m._render_cell("is_open", False), "no")

    def test_is_partial_true_renders_Y(self):
        self.assertEqual(self.m._render_cell("is_partial", True), "Y")

    def test_is_partial_false_renders_N(self):
        self.assertEqual(self.m._render_cell("is_partial", False), "N")

    def test_other_bool_true_renders_TRUE(self):
        for col in ("over_saturation", "outlier_flag", "forecast_exclude"):
            self.assertEqual(self.m._render_cell(col, True), "TRUE", f"Failed for {col}")

    def test_other_bool_false_renders_FALSE(self):
        self.assertEqual(self.m._render_cell("over_saturation", False), "FALSE")

    def test_float_whole_number_renders_as_int(self):
        self.assertEqual(self.m._render_cell("orders", 42.0), 42)

    def test_float_fractional_rounds_to_6(self):
        result = self.m._render_cell("labor_pct", 0.2849999999)
        self.assertAlmostEqual(float(result), 0.285, places=5)

    def test_int_passes_through(self):
        self.assertEqual(self.m._render_cell("txn_count", 100), 100)

    def test_string_passes_through(self):
        self.assertEqual(self.m._render_cell("coverage", "full"), "full")


class TestTabSpecs(unittest.TestCase):
    """Validate that every tab spec has a consistent header / column index spec."""

    def setUp(self):
        self.m = _load()

    def test_all_specs_have_required_keys(self):
        required = {"tab", "bq_table", "sort_by", "header", "currency_cols", "number_cols"}
        for spec in self.m._TAB_SPECS:
            missing = required - set(spec.keys())
            self.assertFalse(missing, f"Spec for {spec.get('tab')} missing keys: {missing}")

    def test_currency_cols_in_range(self):
        for spec in self.m._TAB_SPECS:
            n = len(spec["header"])
            for idx in spec["currency_cols"]:
                self.assertLess(idx, n, f"{spec['tab']}: currency_col {idx} >= header len {n}")

    def test_number_cols_in_range(self):
        for spec in self.m._TAB_SPECS:
            n = len(spec["header"])
            for idx in spec.get("number_cols", []):
                self.assertLess(idx, n, f"{spec['tab']}: number_col {idx} >= header len {n}")

    def test_no_overlap_between_currency_and_number_cols(self):
        for spec in self.m._TAB_SPECS:
            overlap = set(spec["currency_cols"]) & set(spec.get("number_cols", []))
            self.assertFalse(overlap, f"{spec['tab']}: overlap between currency and number cols: {overlap}")

    def test_sort_by_cols_in_header(self):
        for spec in self.m._TAB_SPECS:
            for col in spec["sort_by"]:
                self.assertIn(col, spec["header"], f"{spec['tab']}: sort col {col!r} not in header")

    def test_projector_skips_input_tabs(self):
        """Config and training tabs must not appear in _TAB_SPECS."""
        tab_names = {spec["tab"] for spec in self.m._TAB_SPECS}
        for protected in ("config", "training_excluded", "labor_daily_forecast"):
            self.assertNotIn(protected, tab_names, f"Input tab {protected!r} should not be projected")


class TestReadBqTab(unittest.TestCase):
    """_read_bq_tab returns header-only on BQ read failure (graceful degradation)."""

    def test_returns_header_only_on_error(self):
        m = _load()
        import unittest.mock as mock

        spec = {
            "tab": "daily",
            "bq_table": "model_daily",
            "sort_by": ["date"],
            "header": ["date", "dow"],
            "currency_cols": [],
            "number_cols": [],
        }
        with mock.patch.object(m, "read_query", side_effect=Exception("BQ unavailable")):
            result = m._read_bq_tab(spec)
        self.assertEqual(result, [["date", "dow"]])


if __name__ == "__main__":
    unittest.main()
