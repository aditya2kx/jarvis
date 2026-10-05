#!/usr/bin/env python3
"""Unit tests for runner._goto_next_week — the Sunday-evening week-skip fallback.

Run:
    python3 skills/adp_run_automation/test_runner_week_nav.py

Covers: a › that lands two weeks on is corrected with one ‹ (the skipped week is
read), a normal › is left alone, and a ‹ that does not land on the next week
leaves an uncorrected breadcrumb instead of raising.
"""

from __future__ import annotations

import contextlib
import datetime
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.adp_run_automation import runner


def _label(d: datetime.date) -> str:
    end = d + datetime.timedelta(days=6)
    return f"Week of {d:%b} {d.day}, {d.year} - {end:%b} {end.day}, {end.year}"


class _Grid:
    """A schedule grid whose first › skips a week; ‹ steps back ``back_days``."""

    def __init__(self, first_jump: int, back_days: int = 7):
        self.week = datetime.date(2026, 9, 28)
        self.jumps = [first_jump]
        self.back_days = back_days
        self.prev_clicks = 0

    def next(self):
        self.week += datetime.timedelta(days=self.jumps.pop(0) if self.jumps else 7)

    def prev(self):
        self.prev_clicks += 1
        self.week -= datetime.timedelta(days=self.back_days)


class _Label:
    def __init__(self, grid):
        self.grid = grid

    def inner_text(self, timeout=None):
        return _label(self.grid.week)

    def bounding_box(self):
        return {"x": 0, "y": 0, "width": 200, "height": 20}

    def click(self, **_):
        self.grid.next()


class _Prev:
    def __init__(self, grid):
        self.grid = grid

    def evaluate(self, expr, arg=None, timeout=None):
        self.grid.prev()


class _Locator:
    def __init__(self, target):
        self.first = target


class _Frame:
    def __init__(self, grid):
        self.grid = grid

    def get_by_text(self, *_):
        return _Locator(_Label(self.grid))

    def locator(self, selector):
        assert selector == '[aria-label="Select previous week"]'
        return _Locator(_Prev(self.grid))

    def evaluate(self, _js):
        return str(self.grid.week)  # totals differ per week → phase 2 returns at once


class _Page:
    def wait_for_timeout(self, _ms):
        pass


def _advance(grid) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        runner._goto_next_week(_Page(), _Frame(grid))
    return out.getvalue()


class GotoNextWeekTest(unittest.TestCase):
    def test_skip_is_corrected_with_one_previous_click(self):
        grid = _Grid(first_jump=14)
        log = _advance(grid)
        self.assertEqual(grid.week, datetime.date(2026, 10, 5))
        self.assertEqual(grid.prev_clicks, 1)
        self.assertIn("BREADCRUMB week_skip jump_days=14", log)
        self.assertIn("step=week-skip-corrected", log)
        _advance(grid)
        self.assertEqual(grid.week, datetime.date(2026, 10, 12))

    def test_normal_next_week_never_clicks_previous(self):
        grid = _Grid(first_jump=7)
        log = _advance(grid)
        self.assertEqual(grid.week, datetime.date(2026, 10, 5))
        self.assertEqual(grid.prev_clicks, 0)
        self.assertNotIn("week_skip", log)

    def test_failed_correction_leaves_breadcrumb_and_continues(self):
        grid = _Grid(first_jump=14, back_days=14)
        log = _advance(grid)
        self.assertEqual(grid.prev_clicks, 1)
        self.assertIn("BREADCRUMB week_skip_uncorrected", log)


if __name__ == "__main__":
    unittest.main()
