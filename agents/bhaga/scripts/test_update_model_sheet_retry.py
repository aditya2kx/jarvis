#!/usr/bin/env python3
"""Tests for the Sheets 429 resilience + write-burst reduction in
agents.bhaga.scripts.update_model_sheet.

Two concerns, both born from cloud exec bhaga-daily-refresh-4gtwj dying on
HTTP 429 RESOURCE_EXHAUSTED ("Write requests per minute per user" = 60):

  1. ``_api`` now retries transient 429/5xx via core.sheets_retry — survives a
     burst that crosses the quota window instead of failing the whole step.
  2. ``write_tab_formatting`` coalesces every tab's reset/bold/currency/percent/
     number/resize/hide styling into a SINGLE batchUpdate, so the rebuild no
     longer fires ~7 batchUpdates per tab (×9 tabs) and bursts past 60/min.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import update_model_sheet as ums
from core import sheets_retry


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://sheets.googleapis.com/x", code, "err", {}, io.BytesIO(body.encode())
    )


class _FakeResp:
    def __init__(self, body: str):
        self._b = body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


class ApiRetryTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(sheets_retry.time, "sleep", lambda *_a: None)
        p.start()
        self.addCleanup(p.stop)

    def test_retries_429_then_succeeds(self):
        seq = [
            _http_error(429, '{"error": {"status": "RESOURCE_EXHAUSTED"}}'),
            _http_error(429, '{"error": {"status": "RESOURCE_EXHAUSTED"}}'),
            _FakeResp('{"replies": []}'),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=seq) as m:
            out = ums._api(
                f"{ums.SHEETS_API}/spreadsheets/sid:batchUpdate",
                "tok", method="POST", data={"requests": []},
            )
        self.assertEqual(out, {"replies": []})
        self.assertEqual(m.call_count, 3)

    def test_gives_up_after_max_attempts(self):
        seq = [
            _http_error(429, '{"error": {"status": "RESOURCE_EXHAUSTED"}}')
            for _ in range(sheets_retry.DEFAULT_MAX_ATTEMPTS)
        ]
        with mock.patch("urllib.request.urlopen", side_effect=seq) as m:
            with self.assertRaises(RuntimeError) as ctx:
                ums._api(
                    f"{ums.SHEETS_API}/spreadsheets/sid:batchUpdate",
                    "tok", method="POST", data={"requests": []},
                )
        self.assertIn("HTTP 429", str(ctx.exception))
        self.assertEqual(m.call_count, sheets_retry.DEFAULT_MAX_ATTEMPTS)


class FormatBatchingTest(unittest.TestCase):
    """write_tab_formatting collapses N tabs' styling into one batchUpdate."""

    def _specs(self):
        return [
            {"sheet_id": 11, "num_cols": 5, "currency_cols": [2, 3],
             "percent_cols": [4], "seconds_cols": [], "hidden_cols": []},
            {"sheet_id": 22, "num_cols": 3, "currency_cols": [],
             "percent_cols": [], "seconds_cols": [1], "hidden_cols": [0]},
        ]

    def test_single_batchupdate_call(self):
        calls = []

        def fake_api(url, token, *, method="GET", data=None):
            calls.append((method, url, data))
            return {}

        with mock.patch.object(ums, "_api", fake_api):
            n = ums.write_tab_formatting("sid", "tok", self._specs())

        self.assertEqual(n, 1)
        self.assertEqual(len(calls), 1, "all tab formatting must coalesce to ONE batchUpdate")
        method, url, data = calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith(":batchUpdate"))
        self.assertIn("requests", data)

    def test_formatting_requests_preserved(self):
        reqs = []
        for spec in self._specs():
            reqs += ums.build_tab_format_requests(
                sheet_id=spec["sheet_id"], num_cols=spec["num_cols"],
                currency_cols=spec["currency_cols"], percent_cols=spec["percent_cols"],
                seconds_cols=spec["seconds_cols"], hidden_cols=spec["hidden_cols"],
            )

        def kind(r):
            return next(iter(r))

        # One userEnteredFormat-wipe (reset) per tab, ordered before its styling.
        resets = [r for r in reqs if kind(r) == "repeatCell"
                  and r["repeatCell"]["fields"] == "userEnteredFormat"]
        self.assertEqual(len(resets), 2)

        # Sheet 11: 2 currency cols + 1 percent col survive.
        currency = [r for r in reqs if kind(r) == "repeatCell"
                    and r["repeatCell"].get("cell", {}).get("userEnteredFormat", {})
                    .get("numberFormat", {}).get("type") == "CURRENCY"]
        self.assertEqual(len(currency), 2)
        percent = [r for r in reqs if kind(r) == "repeatCell"
                   and r["repeatCell"].get("cell", {}).get("userEnteredFormat", {})
                   .get("numberFormat", {}).get("type") == "PERCENT"]
        self.assertEqual(len(percent), 1)

        # Sheet 22: 1 NUMBER (seconds) col + 1 hidden col.
        number = [r for r in reqs if kind(r) == "repeatCell"
                  and r["repeatCell"].get("cell", {}).get("userEnteredFormat", {})
                  .get("numberFormat", {}).get("type") == "NUMBER"]
        self.assertEqual(len(number), 1)
        hides = [r for r in reqs if kind(r) == "updateDimensionProperties"
                 and r["updateDimensionProperties"]["properties"].get("hiddenByUser")]
        self.assertEqual(len(hides), 1)

        # Reset must come before the bold/currency styling for the same sheet.
        first_sheet_reqs = [r for r in reqs
                            if r.get("repeatCell", {}).get("range", {}).get("sheetId") == 11]
        self.assertEqual(first_sheet_reqs[0]["repeatCell"]["fields"], "userEnteredFormat")

    def test_empty_specs_no_call(self):
        with mock.patch.object(ums, "_api", side_effect=AssertionError("should not call")):
            self.assertEqual(ums.write_tab_formatting("sid", "tok", []), 0)


if __name__ == "__main__":
    unittest.main()
