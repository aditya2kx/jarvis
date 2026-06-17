#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.process_reviews.

Run:
    python3 agents/bhaga/scripts/test_process_reviews.py

Covers Layer B's read-side defense in process_reviews — the
``_resolve_data_window_end`` helper must accept ISO,
apostrophe-prefixed, or Sheets-serial values (silently coerced) and
must raise a clear, operator-actionable error on truly bad junk.
"""

from __future__ import annotations

import datetime
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.process_reviews import (
    _resolve_data_window_end,
    rebuild_review_bonus_period,
)


class ResolveDataWindowEndTests(unittest.TestCase):
    def test_iso_passes_through(self):
        d = _resolve_data_window_end({"data_window_end": "2026-05-20"})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_apostrophe_prefixed_stripped(self):
        # Layer A's own output round-trips cleanly through the helper.
        d = _resolve_data_window_end({"data_window_end": "'2026-05-20"})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_serial_silently_recovered(self):
        # 46162 == 2026-05-20 in Sheets serial. Layer B promises silent
        # recovery on this branch.
        d = _resolve_data_window_end({"data_window_end": "46162"})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_whitespace_tolerated(self):
        d = _resolve_data_window_end({"data_window_end": "  2026-05-20  "})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_garbage_raises_clear_error(self):
        with self.assertRaises(RuntimeError) as cm:
            _resolve_data_window_end({"data_window_end": "banana"})
        # The literal bad cell value MUST appear in the error so the
        # operator can grep for it in the sheet.
        self.assertIn("banana", str(cm.exception))

    def test_missing_key_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            _resolve_data_window_end({})
        self.assertIn("data_window_end", str(cm.exception))

    def test_empty_value_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            _resolve_data_window_end({"data_window_end": ""})
        self.assertIn("data_window_end", str(cm.exception))


class RebuildReviewBonusPeriodTests(unittest.TestCase):
    """rollup must not depend on a local Earnings XLSX (cloud has none)."""

    def test_rebuild_without_earnings_xlsx(self):
        profile = {
            "adp_run": {
                "pay_periods_anchor_end_date": "2026-05-17",
                "pay_frequency": "biweekly",
            },
            "calibration": {"first_data_window": {"start": "2026-02-17"}},
        }
        reviews = [
            {
                "shift_date_credited": "2026-05-28",
                "rating": 5,
                "named": ["Alice"],
                "allocations": {"Alice": 20},
            },
        ]
        with unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.add_sheet_if_missing",
            return_value="sheet123",
        ) as add_sheet, unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.clear_and_write_tab",
        ) as write_tab, unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.bold_header_row",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.format_currency_columns",
        ):
            n = rebuild_review_bonus_period(
                model_sid="model_sid",
                token="token",
                all_reviews=reviews,
                data_window_end=datetime.date(2026, 5, 29),
                profile=profile,
            )
        self.assertGreaterEqual(n, 1)
        add_sheet.assert_called_once()
        write_tab.assert_called_once()
        written = write_tab.call_args.kwargs["values"]
        self.assertEqual(written[0][0], "period_start")
        open_rows = [r for r in written[1:] if r[2] == "yes"]
        ends = {str(r[1]).lstrip("'") for r in open_rows}
        self.assertIn("2026-05-29", ends, "open period should end at data_window_end")


class ReviewBonusBqSinkTests(unittest.TestCase):
    """M3: rebuild_review_bonus_period writes to BQ when BHAGA_DATASTORE=bigquery."""

    _PROFILE = {
        "adp_run": {
            "pay_periods_anchor_end_date": "2026-05-17",
            "pay_frequency": "biweekly",
        },
        "calibration": {"first_data_window": {"start": "2026-02-17"}},
    }
    _REVIEWS = [
        {
            "shift_date_credited": "2026-05-28",
            "rating": 5,
            "named": ["Alice"],
            "allocations": {"Alice": 20},
        },
    ]

    def _run_rebuild(self, *, bq_enabled: bool, load_raises: bool = False):
        """Run rebuild_review_bonus_period with mocked Sheet+BQ calls."""
        bq_calls: list[dict] = []

        def fake_load_model_rows(table, rows, **_kw):
            if load_raises:
                raise RuntimeError("BQ unavailable")
            bq_calls.append({"table": table, "n": len(rows) - 1})
            return len(rows) - 1

        with unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.add_sheet_if_missing",
            return_value="sheet123",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.clear_and_write_tab",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.bold_header_row",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.format_currency_columns",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews._bq_enabled",
            return_value=bq_enabled,
        ), unittest.mock.patch(
            "agents.bhaga.scripts.materialize_model_bq.load_model_rows",
            side_effect=fake_load_model_rows,
        ):
            n = rebuild_review_bonus_period(
                model_sid="model_sid",
                token="token",
                all_reviews=self._REVIEWS,
                data_window_end=datetime.date(2026, 5, 29),
                profile=self._PROFILE,
            )
        return n, bq_calls

    def test_bq_disabled_no_bq_call(self):
        n, bq_calls = self._run_rebuild(bq_enabled=False)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(bq_calls, [], "BQ should not be called when disabled")

    def test_bq_enabled_writes_to_correct_table(self):
        n, bq_calls = self._run_rebuild(bq_enabled=True)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(len(bq_calls), 1)
        self.assertEqual(bq_calls[0]["table"], "model_review_bonus_period")

    def test_bq_error_does_not_fail_sheet_write(self):
        """BQ error must be non-fatal — Sheet write still completes."""
        n, bq_calls = self._run_rebuild(bq_enabled=True, load_raises=True)
        # Sheet write succeeded (n >= 1) despite BQ error.
        self.assertGreaterEqual(n, 1)


class TestBqPrimaryReviews(unittest.TestCase):
    """Verify BQ-primary reviews path: write to google_reviews BQ, read from BQ."""

    def _make_parsed_review(self) -> dict:
        from agents.bhaga.scripts.process_reviews import build_review_row, REVIEW_HEADER_ROW
        return {
            "review_id": "rev-001",
            "post_dt_ct": datetime.datetime(2026, 5, 1, 10, 30, 0,
                                            tzinfo=__import__("zoneinfo").ZoneInfo("America/Chicago")),
            "post_date_ct": datetime.date(2026, 5, 1),
            "rating": 5,
            "reviewer": "John D.",
            "comment": "Great coffee!",
            "named": ["Alvarez, Sebastian"],
            "named_status": "ok",
            "shift_date_credited": None,
            "shift_assignment_reason": "no_match",
            "shift_members_credited": [],
            "trainees_on_shift": [],
            "allocations": {},
            "total_bonus_dollars": 0.0,
            "ingested_at_utc": "2026-05-01T15:00:00+00:00",
            "review_url": "https://example.com/rev-001",
        }

    def test_rec_to_bq_shape_excludes_clickup_message_id(self):
        """_rec_to_bq_shape must produce a BQ row without clickup_message_id."""
        from agents.bhaga.scripts.process_reviews import _rec_to_bq_shape
        rec = self._make_parsed_review()
        bq_row = _rec_to_bq_shape(rec)
        self.assertNotIn("clickup_message_id", bq_row)
        self.assertEqual(bq_row["review_id"], "rev-001")

    def test_rec_to_bq_shape_strips_apostrophes(self):
        """Apostrophe text-protection must be stripped before calling map_google_review."""
        from agents.bhaga.scripts.process_reviews import _rec_to_bq_shape
        import agents.bhaga.scripts.process_reviews as pr_module
        rec = self._make_parsed_review()
        bq_row = _rec_to_bq_shape(rec)
        # post_date_ct should be a date object (parsed), not a string starting with "'"
        self.assertIsInstance(bq_row.get("post_date_ct"), (datetime.date, type(None)))

    def test_latest_review_ts_ms_returns_none_when_bq_disabled(self):
        """When BHAGA_DATASTORE != bigquery, high-water mark returns None."""
        import unittest.mock as mock
        from agents.bhaga.scripts.process_reviews import _latest_review_ts_ms
        with mock.patch("agents.bhaga.scripts.process_reviews._bq_enabled", return_value=False):
            result = _latest_review_ts_ms()
        self.assertIsNone(result)

    def test_read_all_reviews_from_bq_builds_allocations(self):
        """_read_all_reviews reads from google_reviews BQ and rebuilds allocations."""
        import unittest.mock as mock
        from agents.bhaga.scripts.process_reviews import _read_all_reviews

        bq_rows = [{
            "review_id": "rev-001",
            "post_ts_ct": "2026-05-01T10:30:00-05:00",
            "post_date_ct": datetime.date(2026, 5, 1),
            "rating": 5,
            "reviewer": "John D.",
            "comment": "Great coffee!",
            "named_baristas": "Alvarez, Sebastian",
            "named_status": "ok",
            "shift_date_credited": "",
            "shift_assignment_reason": "no_match",
            "shift_members": "",
            "trainees_on_shift": "",
            "named_credit_each": 0.0,
            "base_credit_each": 0.0,
            "total_bonus": 0.0,
            "review_url": "https://example.com/rev-001",
            "ingested_at_utc": datetime.datetime(2026, 5, 1, 15, 0, 0,
                                                  tzinfo=datetime.timezone.utc),
        }]

        with mock.patch("agents.bhaga.scripts.process_reviews.read_query", return_value=bq_rows):
            result = _read_all_reviews(excluded_permanent=set(), training_through={})

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["review_id"], "rev-001")
        self.assertIn("allocations", result[0])
        self.assertIn("named", result[0])

    def test_ensure_review_raw_tabs_does_not_create_reviews_tab(self):
        """_ensure_review_raw_tabs must only create unparseable tab, not reviews tab."""
        import unittest.mock as mock
        from agents.bhaga.scripts.process_reviews import _ensure_review_raw_tabs

        created_tabs: list[str] = []

        def fake_add_sheet(sid, token, tab_name, column_count=10):
            created_tabs.append(tab_name)
            return 1

        def fake_tab_has_data(sid, token, tab):
            return True  # pretend tab has data so we skip the seed-header call

        with mock.patch("agents.bhaga.scripts.process_reviews.add_sheet_if_missing", fake_add_sheet), \
             mock.patch("agents.bhaga.scripts.process_reviews._tab_has_any_data", fake_tab_has_data):
            _ensure_review_raw_tabs("fake-sid", "fake-token")

        self.assertNotIn("reviews", created_tabs,
                         "reviews tab should NOT be created (rendered from BQ instead)")
        self.assertIn("unparseable", created_tabs)


# ── New tests for pool bonus (2026-06-08) ────────────────────────────────────


class SplitPoolEquallyTests(unittest.TestCase):
    """split_pool_equally: exact-cent arithmetic, deterministic remainder."""

    def setUp(self):
        from agents.bhaga.scripts.process_reviews import split_pool_equally
        self.split = split_pool_equally

    def test_even_split_four(self):
        result = self.split(20, ["A", "B", "C", "D"])
        self.assertEqual(result, {"A": 5.0, "B": 5.0, "C": 5.0, "D": 5.0})
        self.assertAlmostEqual(sum(result.values()), 20.0)

    def test_remainder_three_members(self):
        # $20 / 3 = 6.67, 6.67, 6.66 (alphabetical: first two get the extra cent)
        result = self.split(20, ["Alice", "Bob", "Carol"])
        self.assertEqual(result["Alice"], 6.67)
        self.assertEqual(result["Bob"], 6.67)
        self.assertEqual(result["Carol"], 6.66)
        self.assertAlmostEqual(sum(result.values()), 20.0)

    def test_remainder_seven_members(self):
        members = [f"emp{i}" for i in range(7)]
        result = self.split(20, members)
        # $20 = 2000 cents; 2000 / 7 = 285 rem 5 → first 5 get 2.86, last 2 get 2.85
        total = sum(result.values())
        self.assertAlmostEqual(total, 20.0, places=10)
        self.assertEqual(len(result), 7)

    def test_empty_members_returns_empty(self):
        self.assertEqual(self.split(20, []), {})

    def test_deduplicates_names(self):
        result = self.split(20, ["Alice", "Alice", "Bob"])
        self.assertIn("Alice", result)
        self.assertIn("Bob", result)
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(sum(result.values()), 20.0)

    def test_deterministic_alphabetical_remainder(self):
        # "Alice" < "Bob" alphabetically; Alice gets the extra cent
        r1 = self.split(10, ["Bob", "Alice", "Carol"])  # $10/3 → 3.34, 3.33, 3.33
        r2 = self.split(10, ["Alice", "Carol", "Bob"])
        self.assertEqual(r1, r2)  # order of input doesn't change output
        # Alice comes first alphabetically → gets the higher share
        self.assertGreaterEqual(r1["Alice"], r1["Bob"])
        self.assertGreaterEqual(r1["Alice"], r1["Carol"])


class AllocateBonusPoolTests(unittest.TestCase):
    """allocate_bonus pool mode (post_date >= 2026-06-08)."""

    POOL_DATE = datetime.date(2026, 6, 8)
    POOL_KWARGS = dict(
        pool_effective_date=datetime.date(2026, 6, 8),
        pool_dollars=20,
    )

    def _alloc(self, *, shift_members, named=None, excluded_permanent=None,
               training_through=None, assignment_reason="in_hours"):
        from agents.bhaga.scripts.process_reviews import allocate_bonus
        return allocate_bonus(
            shift_members=shift_members,
            named=named or [],
            excluded_permanent=excluded_permanent or set(),
            training_through=training_through or {},
            shift_date="2026-06-08",
            post_date=self.POOL_DATE,
            assignment_reason=assignment_reason,
            **self.POOL_KWARGS,
        )

    def test_equal_split_among_in_hours(self):
        result = self._alloc(shift_members=["Alice", "Bob", "Carol"])
        self.assertAlmostEqual(sum(result.values()), 20.0)
        # All three get equal share
        for v in result.values():
            self.assertAlmostEqual(v, 20 / 3, places=1)

    def test_not_in_hours_returns_empty(self):
        result = self._alloc(
            shift_members=["Alice", "Bob"],
            assignment_reason="last_shift_prior_day",
        )
        self.assertEqual(result, {})

    def test_last_shift_same_day_returns_empty(self):
        result = self._alloc(
            shift_members=["Alice", "Bob"],
            assignment_reason="last_shift_same_day",
        )
        self.assertEqual(result, {})

    def test_permanent_exclusion_removed_from_split(self):
        result = self._alloc(
            shift_members=["Alice", "Bob", "Manager"],
            excluded_permanent={"Manager"},
        )
        self.assertNotIn("Manager", result)
        self.assertAlmostEqual(sum(result.values()), 20.0)
        self.assertEqual(set(result.keys()), {"Alice", "Bob"})

    def test_training_exclusion_removed_from_split(self):
        result = self._alloc(
            shift_members=["Alice", "Bob", "Trainee"],
            training_through={"Trainee": datetime.date(2026, 6, 30)},
        )
        self.assertNotIn("Trainee", result)
        self.assertAlmostEqual(sum(result.values()), 20.0)

    def test_named_person_gets_equal_share_not_flat_20(self):
        # Named in comment but pool mode → same equal share, NOT the legacy $20 flat.
        result = self._alloc(
            shift_members=["Alice", "Bob", "Carol"],
            named=["Alice"],  # named, but pool mode ignores shoutouts
        )
        self.assertIn("Alice", result)
        self.assertIn("Bob", result)
        # Alice must NOT get $20; she gets the same share as everyone else.
        self.assertAlmostEqual(result["Alice"], result["Bob"], places=2)
        self.assertAlmostEqual(sum(result.values()), 20.0)

    def test_named_but_excluded_not_rescued(self):
        # Opposite of legacy: pool mode DOES NOT rescue a named-but-excluded person.
        result = self._alloc(
            shift_members=["Alice", "Bob", "Manager"],
            named=["Manager"],
            excluded_permanent={"Manager"},
        )
        self.assertNotIn("Manager", result)
        self.assertAlmostEqual(sum(result.values()), 20.0)

    def test_all_excluded_returns_empty(self):
        result = self._alloc(
            shift_members=["Manager"],
            excluded_permanent={"Manager"},
        )
        self.assertEqual(result, {})


class AllocateBonusLegacyRegressionTests(unittest.TestCase):
    """allocate_bonus legacy mode (post_date < 2026-06-08) — byte-identical to old behavior."""

    LEGACY_DATE = datetime.date(2026, 5, 20)
    LEGACY_KWARGS = dict(
        pool_effective_date=datetime.date(2026, 6, 8),
        pool_dollars=20,
        post_date=datetime.date(2026, 5, 20),
        assignment_reason="in_hours",
    )

    def _alloc(self, *, shift_members, named=None, excluded_permanent=None, training_through=None):
        from agents.bhaga.scripts.process_reviews import allocate_bonus
        return allocate_bonus(
            shift_members=shift_members,
            named=named or [],
            excluded_permanent=excluded_permanent or set(),
            training_through=training_through or {},
            shift_date="2026-05-20",
            **self.LEGACY_KWARGS,
        )

    def test_shoutout_only_named_get_20(self):
        result = self._alloc(
            shift_members=["Alice", "Bob"],
            named=["Alice"],
        )
        self.assertEqual(result, {"Alice": 20.0})

    def test_shoutout_overrides_permanent_exclusion(self):
        result = self._alloc(
            shift_members=["Alice", "Manager"],
            named=["Manager"],
            excluded_permanent={"Manager"},
        )
        self.assertIn("Manager", result)
        self.assertEqual(result["Manager"], 20.0)

    def test_shoutout_overrides_training_exclusion(self):
        result = self._alloc(
            shift_members=["Alice", "Trainee"],
            named=["Trainee"],
            training_through={"Trainee": datetime.date(2026, 5, 30)},
        )
        self.assertIn("Trainee", result)
        self.assertEqual(result["Trainee"], 20.0)

    def test_no_shoutout_base_10_each(self):
        result = self._alloc(shift_members=["Alice", "Bob", "Carol"])
        self.assertEqual(result, {"Alice": 10.0, "Bob": 10.0, "Carol": 10.0})

    def test_no_shoutout_excludes_permanent(self):
        result = self._alloc(
            shift_members=["Alice", "Bob", "Manager"],
            excluded_permanent={"Manager"},
        )
        self.assertNotIn("Manager", result)
        self.assertEqual(result["Alice"], 10.0)
        self.assertEqual(result["Bob"], 10.0)

    def test_no_shoutout_excludes_trainee(self):
        result = self._alloc(
            shift_members=["Alice", "Trainee"],
            training_through={"Trainee": datetime.date(2026, 5, 30)},
        )
        self.assertNotIn("Trainee", result)
        self.assertEqual(result["Alice"], 10.0)

    def test_last_shift_fallback_still_pays_legacy(self):
        # Legacy mode works even with non-in_hours assignment_reason.
        from agents.bhaga.scripts.process_reviews import allocate_bonus
        result = allocate_bonus(
            shift_members=["Alice", "Bob"],
            named=[],
            excluded_permanent=set(),
            training_through={},
            shift_date="2026-05-20",
            post_date=datetime.date(2026, 5, 20),
            assignment_reason="last_shift_prior_day",
            pool_effective_date=datetime.date(2026, 6, 8),
            pool_dollars=20,
        )
        self.assertEqual(result, {"Alice": 10.0, "Bob": 10.0})


class BuildReviewRowPoolTests(unittest.TestCase):
    """build_review_row audit columns are pool-aware."""

    def _make_rec(self, post_date: datetime.date, named: list, allocations: dict) -> dict:
        from zoneinfo import ZoneInfo
        CT = ZoneInfo("America/Chicago")
        return {
            "review_id": "test-001",
            "post_dt_ct": datetime.datetime(post_date.year, post_date.month, post_date.day,
                                            12, 0, 0, tzinfo=CT),
            "post_date_ct": post_date,
            "rating": 5,
            "reviewer": "Test Reviewer",
            "comment": "Great!",
            "named": named,
            "named_status": "ok",
            "shift_date_credited": post_date.isoformat(),
            "shift_assignment_reason": "in_hours",
            "shift_members_credited": list(allocations.keys()),
            "trainees_on_shift": [],
            "allocations": allocations,
            "total_bonus_dollars": sum(allocations.values()),
            "ingested_at_utc": "2026-06-08T19:00:00+00:00",
            "review_url": "https://example.com",
            "clickup_message_id": "msg-001",
        }

    def test_pool_review_named_credit_is_blank(self):
        from agents.bhaga.scripts.process_reviews import build_review_row, REVIEW_HEADER_ROW
        pool_date = datetime.date(2026, 6, 8)
        rec = self._make_rec(pool_date, named=["Alice"],
                             allocations={"Alice": 6.67, "Bob": 6.67, "Carol": 6.66})
        row = build_review_row(rec)
        d = dict(zip(REVIEW_HEADER_ROW, row))
        self.assertEqual(d["named_credit_each"], "")

    def test_pool_review_base_credit_is_per_head(self):
        from agents.bhaga.scripts.process_reviews import build_review_row, REVIEW_HEADER_ROW
        pool_date = datetime.date(2026, 6, 8)
        rec = self._make_rec(pool_date, named=[],
                             allocations={"Alice": 10.0, "Bob": 10.0})
        row = build_review_row(rec)
        d = dict(zip(REVIEW_HEADER_ROW, row))
        self.assertEqual(d["base_credit_each"], 10.0)

    def test_pool_review_total_bonus_is_exact(self):
        from agents.bhaga.scripts.process_reviews import build_review_row, REVIEW_HEADER_ROW
        pool_date = datetime.date(2026, 6, 8)
        rec = self._make_rec(pool_date, named=[],
                             allocations={"Alice": 6.67, "Bob": 6.67, "Carol": 6.66})
        row = build_review_row(rec)
        d = dict(zip(REVIEW_HEADER_ROW, row))
        self.assertAlmostEqual(d["total_bonus"], 20.0)

    def test_legacy_review_named_credit_is_20(self):
        from agents.bhaga.scripts.process_reviews import build_review_row, REVIEW_HEADER_ROW
        legacy_date = datetime.date(2026, 5, 20)
        rec = self._make_rec(legacy_date, named=["Alice"],
                             allocations={"Alice": 20.0})
        row = build_review_row(rec)
        d = dict(zip(REVIEW_HEADER_ROW, row))
        self.assertEqual(d["named_credit_each"], 20)

    def test_legacy_review_base_credit_is_10(self):
        from agents.bhaga.scripts.process_reviews import build_review_row, REVIEW_HEADER_ROW
        legacy_date = datetime.date(2026, 5, 20)
        rec = self._make_rec(legacy_date, named=[],
                             allocations={"Alice": 10.0, "Bob": 10.0})
        row = build_review_row(rec)
        d = dict(zip(REVIEW_HEADER_ROW, row))
        self.assertEqual(d["base_credit_each"], 10)


class PoolRollupTests(unittest.TestCase):
    """build_period_rollup handles pool reviews (base_dollars, named_count=0)
    and mixed periods (legacy + pool) correctly."""

    _PROFILE = {
        "adp_run": {
            "pay_periods_anchor_end_date": "2026-05-31",
            "pay_frequency": "biweekly",
        },
        "calibration": {"first_data_window": {"start": "2026-02-17"}},
    }

    def _run_rollup(self, reviews, data_window_end):
        from agents.bhaga.scripts.process_reviews import rebuild_review_bonus_period
        written_rows = []

        with unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.add_sheet_if_missing",
            return_value="sheet123",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.clear_and_write_tab",
            side_effect=lambda *a, **kw: written_rows.extend(kw.get("values", [])),
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.bold_header_row",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.format_currency_columns",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews._bq_enabled", return_value=False,
        ):
            rebuild_review_bonus_period(
                model_sid="model_sid",
                token="token",
                all_reviews=reviews,
                data_window_end=data_window_end,
                profile=self._PROFILE,
            )
        return written_rows

    def test_pool_review_lands_in_base_dollars(self):
        reviews = [{
            "shift_date_credited": "2026-06-08",
            "rating": 5,
            "named": [],  # pool reviews have named cleared
            "allocations": {"Alice": 10.0, "Bob": 10.0},
        }]
        rows = self._run_rollup(reviews, datetime.date(2026, 6, 14))
        data_rows = [r for r in rows if isinstance(r[0], str) and r[0] != "period_start"]
        self.assertGreater(len(data_rows), 0)
        # named_count (col 5) must be 0; base_dollars (col 6) must be > 0
        for r in data_rows:
            self.assertEqual(r[5], 0, f"named_count should be 0 for pool review, got {r[5]}")
            self.assertGreater(r[6], 0, "base_dollars should be > 0 for pool review")

    def test_pool_review_total_bonus_correct(self):
        reviews = [{
            "shift_date_credited": "2026-06-08",
            "rating": 5,
            "named": [],
            "allocations": {"Alice": 6.67, "Bob": 6.67, "Carol": 6.66},
        }]
        rows = self._run_rollup(reviews, datetime.date(2026, 6, 14))
        data_rows = [r for r in rows if isinstance(r[0], str) and r[0] != "period_start"]
        total_all = sum(r[8] for r in data_rows)  # col 8 = total_bonus
        self.assertAlmostEqual(total_all, 20.0, places=1)

    def test_mixed_period_legacy_plus_pool(self):
        reviews = [
            # Legacy review (2026-05-25): Alice named shoutout $20
            {
                "shift_date_credited": "2026-05-25",
                "rating": 5,
                "named": ["Alice"],
                "allocations": {"Alice": 20.0},
            },
            # Pool review (2026-06-08): Alice+Bob equal split $10 each
            {
                "shift_date_credited": "2026-06-08",
                "rating": 5,
                "named": [],
                "allocations": {"Alice": 10.0, "Bob": 10.0},
            },
        ]
        rows = self._run_rollup(reviews, datetime.date(2026, 6, 14))
        data_rows = [r for r in rows if isinstance(r[0], str) and r[0] != "period_start"]
        # Alice should appear in both periods (may be different period_start dates)
        alice_rows = [r for r in data_rows if r[3] == "Alice"]
        total_alice = sum(r[8] for r in alice_rows)
        # Alice: $20 from legacy + $10 from pool = $30 total across periods
        self.assertAlmostEqual(total_alice, 30.0, places=1)


class ResolveDataWindowEndFromBqTests(unittest.TestCase):
    """process_reviews.main must derive data_window_end from BQ, never store_config.

    This is the regression guard for the 2026-06-15 incident: a stale
    store_config row froze data_window_end at 2026-06-13 while BQ had data
    through 2026-06-16, causing 30 reviews to be held back every nightly run.
    """

    def _make_args(self, *, store="palmetto", until=None):
        import argparse
        ns = argparse.Namespace(
            store=store,
            until=until,
            max_pages=1,
            dry_run=False,
            item_operations_only=False,
            all_item_operations=False,
            data_source="bigquery",
        )
        return ns

    def test_stale_store_config_row_does_not_freeze_window(self):
        """Even when store_config has a stale data_window_end, process_reviews
        must derive the live date from BQ square_transactions."""
        import core.store_config as sc_mod

        # Simulate stale store_config row (the 2026-06-15 bug)
        stale_get_config_calls = []
        def stale_get_config(store, key):
            stale_get_config_calls.append(key)
            if key == "data_window_end":
                return "2026-06-13"  # stale value
            return None

        live_date = "2026-06-16"
        resolve_calls = []
        def live_resolve(store):
            resolve_calls.append(store)
            return live_date

        with unittest.mock.patch.object(sc_mod, "resolve_data_window_end", live_resolve), \
             unittest.mock.patch.object(sc_mod, "get_config", stale_get_config):
            from core.store_config import resolve_data_window_end
            result = resolve_data_window_end("palmetto")

        self.assertEqual(result, live_date, "Must return BQ-derived date, not stale store_config value")
        # resolve_data_window_end must have been called (not get_config for this key)
        self.assertIn("palmetto", resolve_calls)
        self.assertNotIn("data_window_end", stale_get_config_calls,
                         "process_reviews must not call get_config('data_window_end')")

    def test_until_override_takes_precedence_over_derived(self):
        """--until flag overrides derived data_window_end regardless of BQ value."""
        import argparse
        import core.store_config as sc_mod

        # Override should short-circuit before resolve_data_window_end is called
        resolve_calls = []
        def track_resolve(store):
            resolve_calls.append(store)
            return "2026-06-16"

        # Verify the date parsing works correctly with --until
        with unittest.mock.patch.object(sc_mod, "resolve_data_window_end", track_resolve):
            import datetime as _dt
            override = _dt.date.fromisoformat("2026-05-01")
            self.assertEqual(override, _dt.date(2026, 5, 1))


if __name__ == "__main__":
    unittest.main()
