"""Tests for the JSON-driven item-sales selectors + resilient pill finder.

These verify the 2026-05-31 'date picker not found' class of drift is now a
one-file selector edit: the patterns/locators come from item_sales.json (with
built-in fallbacks) and the finder tries them in order."""

from __future__ import annotations

import pytest

from skills.square_tips import runner


@pytest.fixture(autouse=True)
def _reset_selector_cache():
    runner._item_sales_selectors_cache = None
    yield
    runner._item_sales_selectors_cache = None


class _FakeLocator:
    def __init__(self, page, key):
        self.page = page
        self.key = key

    def filter(self, has_text=None):
        # has_text is a compiled regex; the filter key is its pattern string.
        return _FakeLocator(self.page, has_text.pattern if has_text else self.key)

    @property
    def first(self):
        return self

    def wait_for(self, state=None, timeout=None):
        if self.key in self.page.visible:
            return
        raise RuntimeError(f"locator not visible: {self.key}")


class _FakePage:
    """Visible keys are either a button-filter regex pattern or a css selector."""

    def __init__(self, visible):
        self.visible = set(visible)
        self.url = "https://app.squareup.com/dashboard/sales/reports/item-sales"

    def locator(self, selector):
        return _FakeLocator(self, selector)


class TestSelectorLoading:
    def test_loads_machine_block_from_json(self):
        sel = runner._item_sales_selectors()
        patterns = sel["date_picker"]["pill_text_patterns"]
        assert isinstance(patterns, list) and patterns
        # The MM/DD/YYYY pattern is the primary (current) format.
        assert any("d{2}" in p for p in patterns)
        assert sel["export"]["detail_csv_patterns"]

    def test_falls_back_to_defaults_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "_SELECTORS_DIR", tmp_path)  # no item_sales.json here
        runner._item_sales_selectors_cache = None
        sel = runner._item_sales_selectors()
        assert sel["date_picker"]["pill_text_patterns"] == (
            runner._ITEM_SALES_SELECTOR_DEFAULTS["date_picker"]["pill_text_patterns"]
        )


class TestFindPill:
    def test_found_via_primary_data_hook(self):
        # 2026-06-02 drift: the unified date-filter dropdown's stable test hook is
        # tried FIRST, before the text/structural fallbacks.
        page = _FakePage(visible={"[data-test-sq-date-filter-dropdown-trigger]"})
        assert runner._find_item_sales_pill(page) is not None

    def test_primary_hook_present_in_defaults(self):
        sel = runner._item_sales_selectors()
        assert "[data-test-sq-date-filter-dropdown-trigger]" in sel["date_picker"]["primary_locators"]
        assert sel["date_picker"]["range_input_selectors"]["start"] == ".begin-date input.input-date"
        assert sel["date_picker"]["range_input_selectors"]["end"] == ".end-date input.input-date"

    def test_found_via_primary_pattern(self):
        page = _FakePage(visible={r"\d{2}/\d{2}/\d{4}"})
        assert runner._find_item_sales_pill(page) is not None

    def test_found_via_month_label_fallback(self):
        page = _FakePage(visible={r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d"})
        assert runner._find_item_sales_pill(page) is not None

    def test_found_via_structural_locator_fallback(self):
        # No text pattern matches; a structural locator (aria-haspopup) does.
        page = _FakePage(visible={"button[aria-haspopup='dialog']"})
        assert runner._find_item_sales_pill(page) is not None

    def test_returns_none_when_nothing_matches(self):
        page = _FakePage(visible=set())
        assert runner._find_item_sales_pill(page) is None
