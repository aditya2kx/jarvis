"""Unit tests for migrate_inputs_to_bq — open-period-only guard."""
import datetime
import io
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from agents.bhaga.scripts import migrate_inputs_to_bq as _mod


_PROFILE = {
    "google_sheets": {
        "bhaga_model": {"spreadsheet_id": "fake-sid"},
    },
    "adp_run": {
        # Anchor 2026-05-17, Biweekly → closed period ends 2026-05-31,
        # open period starts 2026-06-01.
        "pay_periods_anchor_end_date": "2026-05-17",
        "pay_frequency": "Biweekly",
    },
}

# "today" used by _today_central in most tests (inside the open period)
_TODAY_CT = datetime.date(2026, 6, 10)


def _sheet_response(dates: list[str]) -> mock.MagicMock:
    """Fake Sheet API response for the given ISO dates."""
    rows = [["Employee, Test", d, "note"] for d in dates]
    payload = json.dumps({"values": [["employee_name", "date", "note"]] + rows})
    cm = mock.MagicMock()
    cm.__enter__ = mock.Mock(return_value=io.BytesIO(payload.encode()))
    cm.__exit__ = mock.Mock(return_value=False)
    return cm


def _run(
    dates: list[str],
    *,
    today: datetime.date = _TODAY_CT,
    open_period_only: bool = True,
) -> list[dict]:
    """Call migrate_training_shifts and return the rows that would be MERGEd."""
    captured: list[dict] = []

    def _fake_load_rows(_table, rows, **_kw):
        captured.extend(rows)
        return len(rows)

    with (
        mock.patch.object(_mod, "refresh_access_token", return_value="tok"),
        mock.patch.object(_mod, "resolve_sheet_id", return_value="fake-sid"),
        mock.patch("urllib.request.urlopen", return_value=_sheet_response(dates)),
        mock.patch.object(_mod, "load_rows", side_effect=_fake_load_rows),
        mock.patch.object(_mod, "_today_central", return_value=today),
    ):
        _mod.migrate_training_shifts(
            _PROFILE, "palmetto",
            dry_run=False,
            open_period_only=open_period_only,
        )

    return captured


class TestOpenPeriodFilter(unittest.TestCase):
    """Verify open_period_only guard in migrate_training_shifts."""

    def test_open_period_row_is_ingested(self):
        """A row dated within the current open pay period must be MERGEd."""
        rows = _run(["2026-06-05"])  # open period: 2026-06-01 onwards
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-06-05")

    def test_closed_period_row_is_skipped(self):
        """A row dated in a closed/paid pay period must NOT be ingested."""
        rows = _run(["2026-05-20"])  # closed period ended 2026-05-31
        self.assertEqual(rows, [])

    def test_mixed_rows_only_open_period_ingested(self):
        """When closed and open rows both present, only open row survives."""
        rows = _run(["2026-05-20", "2026-06-05"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-06-05")

    def test_allow_closed_periods_ingests_all(self):
        """With open_period_only=False all rows are ingested regardless."""
        rows = _run(["2026-05-20", "2026-06-05"], open_period_only=False)
        self.assertEqual(len(rows), 2)
        dates = {r["date"] for r in rows}
        self.assertEqual(dates, {"2026-05-20", "2026-06-05"})

    def test_boundary_row_on_open_start_is_ingested(self):
        """A row exactly on the first day of the open period (2026-06-01) is kept."""
        rows = _run(["2026-06-01"])  # open_start = 2026-06-01
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-06-01")

    def test_boundary_row_on_closed_end_is_skipped(self):
        """A row on the last day of the closed period (2026-05-31) is skipped."""
        rows = _run(["2026-05-31"])  # closed_end = 2026-05-31
        self.assertEqual(rows, [])

    def test_missing_anchor_raises_when_guard_on(self):
        """Missing pay_periods_anchor_end_date is a hard error under open_period_only=True."""
        bad_profile = {
            "google_sheets": {"bhaga_model": {"spreadsheet_id": "fake-sid"}},
            "adp_run": {},  # no anchor
        }
        captured: list[dict] = []

        def _fake_load(_t, rows, **_kw):
            captured.extend(rows)

        with (
            mock.patch.object(_mod, "refresh_access_token", return_value="tok"),
            mock.patch.object(_mod, "resolve_sheet_id", return_value="fake-sid"),
            mock.patch("urllib.request.urlopen", return_value=_sheet_response(["2026-06-05"])),
            mock.patch.object(_mod, "load_rows", side_effect=_fake_load),
            mock.patch.object(_mod, "_today_central", return_value=_TODAY_CT),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _mod.migrate_training_shifts(
                    bad_profile, "palmetto",
                    dry_run=False,
                    open_period_only=True,
                )
        self.assertIn("pay_periods_anchor_end_date", str(ctx.exception))
        self.assertEqual(captured, [], "no rows must be written on guard error")

    def test_real_palmetto_profile_has_required_keys(self):
        """Real palmetto.json must have the keys the guard depends on."""
        import json, pathlib
        profile_path = (
            pathlib.Path(__file__).resolve().parents[1]
            / "knowledge-base" / "store-profiles" / "palmetto.json"
        )
        profile = json.loads(profile_path.read_text())
        adp = profile.get("adp_run", {})
        self.assertIn(
            "pay_periods_anchor_end_date", adp,
            "palmetto.json must have adp_run.pay_periods_anchor_end_date for the open-period guard",
        )
        self.assertIn("pay_frequency", adp)
        import datetime
        datetime.date.fromisoformat(adp["pay_periods_anchor_end_date"])


if __name__ == "__main__":
    unittest.main()
