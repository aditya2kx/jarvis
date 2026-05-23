#!/usr/bin/env python3
"""Unit tests for the target_date-aware Layer A freshness check.

Background — the 2026-05-23 silent partial-success incident
==========================================================
The original `download_adp_bundle` Layer A check was a plain
`is_fresh_download(path, ...)` against
`DOWNLOADS_DIR / f"Timecard-{today_ct.isoformat()}.xlsx"`. The mtime
check ("file modified after CT-midnight today") was the only freshness
signal — the filename encodes the *download* date, not the *target_date*
the run was asking for. That meant an XLSX written earlier the same day
for a DIFFERENT target_date could not be told apart from one written
just now for the current target_date.

On 2026-05-23:
  * 12:08 — orphan run for `--date 2026-05-21` downloaded
    `Timecard-2026-05-23.xlsx`.
  * 12:34 — combined recovery run for `--date 2026-05-22` saw the file,
    declared it fresh on disk, and skipped the ADP scrape entirely.

The fix is a sidecar JSON file (`Timecard-<today>.xlsx.target-meta.json`)
that records the target_date the file was actually scraped for. The
freshness check now requires the sidecar to exist AND match the current
target_date in addition to the mtime check.

Coverage:
  * `_write_target_meta` writes the sidecar with the right shape.
  * `_xlsx_fresh_for_target` accepts only files that pass mtime AND
    sidecar-target-date checks.
  * Missing-sidecar means stale (conservative — forces re-scrape once).
  * Mismatched-target-date means stale.
  * Matched-target-date + fresh mtime means fresh.

Run:
    python3 -m unittest skills.adp_run_automation.test_runner_layer_a -v
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.adp_run_automation.runner import (
    _target_meta_path,
    _write_target_meta,
    _xlsx_fresh_for_target,
)


class TargetMetaPathTests(unittest.TestCase):
    def test_sidecar_path_appends_target_meta_json(self) -> None:
        xlsx = pathlib.Path("/tmp/Timecard-2026-05-23.xlsx")
        self.assertEqual(
            _target_meta_path(xlsx),
            pathlib.Path("/tmp/Timecard-2026-05-23.xlsx.target-meta.json"),
        )


class WriteTargetMetaTests(unittest.TestCase):
    def test_writes_target_date_and_filename(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            xlsx = pathlib.Path(td) / "Timecard-2026-05-23.xlsx"
            xlsx.write_bytes(b"\x00" * 20_000)  # plausible XLSX bulk
            target = datetime.date(2026, 5, 22)
            _write_target_meta(xlsx, target)

            sidecar = _target_meta_path(xlsx)
            self.assertTrue(sidecar.exists())
            data = json.loads(sidecar.read_text())
            self.assertEqual(data["target_date"], "2026-05-22")
            self.assertEqual(data["xlsx_filename"], "Timecard-2026-05-23.xlsx")
            self.assertIn("downloaded_at", data)

    def test_handles_none_target_date(self) -> None:
        # Standalone download_timecard with target_date=None (backfill).
        with tempfile.TemporaryDirectory() as td:
            xlsx = pathlib.Path(td) / "Timecard-2026-05-23.xlsx"
            xlsx.write_bytes(b"\x00" * 20_000)
            _write_target_meta(xlsx, None)
            data = json.loads(_target_meta_path(xlsx).read_text())
            self.assertIsNone(data["target_date"])


class XlsxFreshForTargetTests(unittest.TestCase):
    """The 2026-05-23 fix lives here. These cases must all pass."""

    def _make_fresh_xlsx(self, td: pathlib.Path, name: str = "Timecard-x.xlsx") -> pathlib.Path:
        # Plausibly-sized XLSX with a CT-today mtime so is_fresh_download
        # underneath returns True.
        xlsx = td / name
        xlsx.write_bytes(b"\x00" * 20_000)
        os.utime(xlsx, (time.time(), time.time()))
        return xlsx

    def test_returns_false_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = pathlib.Path(td) / "nope.xlsx"
            self.assertFalse(
                _xlsx_fresh_for_target(
                    missing,
                    target_date=datetime.date(2026, 5, 22),
                    min_bytes=10_000,
                )
            )

    def test_returns_false_when_sidecar_missing(self) -> None:
        # A file from before this fix landed — no sidecar exists. We
        # MUST treat it as stale rather than reuse it, because we
        # cannot prove it was scraped for the right target_date.
        with tempfile.TemporaryDirectory() as td:
            xlsx = self._make_fresh_xlsx(pathlib.Path(td))
            self.assertFalse(
                _xlsx_fresh_for_target(
                    xlsx,
                    target_date=datetime.date(2026, 5, 22),
                    min_bytes=10_000,
                )
            )

    def test_returns_false_on_target_date_mismatch(self) -> None:
        # The 2026-05-23 scenario: file was scraped for target=2026-05-21
        # but this run wants target=2026-05-22. MUST be treated as stale.
        with tempfile.TemporaryDirectory() as td:
            xlsx = self._make_fresh_xlsx(pathlib.Path(td))
            _write_target_meta(xlsx, datetime.date(2026, 5, 21))
            self.assertFalse(
                _xlsx_fresh_for_target(
                    xlsx,
                    target_date=datetime.date(2026, 5, 22),
                    min_bytes=10_000,
                )
            )

    def test_returns_true_on_target_date_match(self) -> None:
        # Healthy crash-resume case: the same orchestrator process restarts
        # within the day, the XLSX is on disk for the CURRENT target_date,
        # and Layer A correctly skips the scrape.
        with tempfile.TemporaryDirectory() as td:
            xlsx = self._make_fresh_xlsx(pathlib.Path(td))
            _write_target_meta(xlsx, datetime.date(2026, 5, 22))
            self.assertTrue(
                _xlsx_fresh_for_target(
                    xlsx,
                    target_date=datetime.date(2026, 5, 22),
                    min_bytes=10_000,
                )
            )

    def test_returns_false_when_file_too_small_even_with_matching_sidecar(self) -> None:
        # Defense in depth: even if the sidecar claims a match, an
        # under-min_bytes file is corrupted / partial and must be re-scraped.
        with tempfile.TemporaryDirectory() as td:
            xlsx = pathlib.Path(td) / "Timecard-stub.xlsx"
            xlsx.write_bytes(b"\x00" * 50)  # well under 10_000
            _write_target_meta(xlsx, datetime.date(2026, 5, 22))
            self.assertFalse(
                _xlsx_fresh_for_target(
                    xlsx,
                    target_date=datetime.date(2026, 5, 22),
                    min_bytes=10_000,
                )
            )

    def test_returns_false_on_unreadable_sidecar(self) -> None:
        # Sidecar exists but isn't valid JSON. Treat as stale.
        with tempfile.TemporaryDirectory() as td:
            xlsx = self._make_fresh_xlsx(pathlib.Path(td))
            _target_meta_path(xlsx).write_text("{not valid json")
            self.assertFalse(
                _xlsx_fresh_for_target(
                    xlsx,
                    target_date=datetime.date(2026, 5, 22),
                    min_bytes=10_000,
                )
            )

    def test_returns_false_when_file_stale_mtime(self) -> None:
        # Even with a matching sidecar, a file last touched BEFORE CT
        # midnight today is stale.
        with tempfile.TemporaryDirectory() as td:
            xlsx = pathlib.Path(td) / "Timecard-y.xlsx"
            xlsx.write_bytes(b"\x00" * 20_000)
            # Two days ago.
            old = time.time() - 2 * 86_400
            os.utime(xlsx, (old, old))
            _write_target_meta(xlsx, datetime.date(2026, 5, 22))
            self.assertFalse(
                _xlsx_fresh_for_target(
                    xlsx,
                    target_date=datetime.date(2026, 5, 22),
                    min_bytes=10_000,
                )
            )


if __name__ == "__main__":
    unittest.main()
