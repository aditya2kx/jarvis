"""Re-seeding a sandbox slot must survive a header spec that has since grown.

Regression for PR #291's live sandbox run, which died in provisioning with
``Range (transactions!AI1) exceeds grid limits. Max rows: 1522, max columns: 33``
— the slot sheet predated a wider header spec, and values.batchUpdate does not
grow a grid the way appending rows does.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agents.bhaga.scripts import bootstrap_sheets as bs


def _specs(n_cols: int, tab: str = "transactions") -> list[dict]:
    return [{"tab_name": tab, "header": [f"c{i}" for i in range(n_cols)], "notes": "n"}]


class TestWidenTabsToFit(unittest.TestCase):
    def _run(
        self,
        *,
        have_cols: int,
        header_cols: int,
        tab: str = "transactions",
        sheet_title: str | None = None,
    ):
        """Return (widened, batchUpdate_payload_or_None)."""
        props = {
            "sheets": [{
                "properties": {
                    "sheetId": 7,
                    "title": sheet_title or tab,
                    "gridProperties": {"columnCount": have_cols},
                }
            }]
        }
        calls = []

        def fake_api(url, token, *, method="GET", data=None):
            calls.append((method, url, data))
            return props if method == "GET" else {}

        with mock.patch.object(bs, "api_request", side_effect=fake_api):
            widened = bs.widen_tabs_to_fit("tok", "sid", _specs(header_cols, tab))
        writes = [c for c in calls if c[0] == "POST"]
        return widened, (writes[0][2] if writes else None)

    def test_widens_by_exactly_the_shortfall(self):
        """33 columns, a 33-wide header: notes land at column 35, so add 2."""
        widened, payload = self._run(have_cols=33, header_cols=33)
        self.assertEqual(widened, {"transactions": 2})
        self.assertEqual(
            payload["requests"],
            [{"appendDimension": {"sheetId": 7, "dimension": "COLUMNS", "length": 2}}],
        )

    def test_wide_enough_sheet_is_left_alone(self):
        widened, payload = self._run(have_cols=40, header_cols=33)
        self.assertEqual(widened, {})
        self.assertIsNone(payload, "must not issue a no-op batchUpdate")

    def test_exact_fit_including_notes_column_is_left_alone(self):
        """header + 2 is the requirement, not header + 3 — don't grow forever."""
        widened, _ = self._run(have_cols=35, header_cols=33)
        self.assertEqual(widened, {})

    def test_tabs_outside_the_spec_are_ignored(self):
        """A narrow tab nobody is seeding is none of our business."""
        widened, payload = self._run(
            have_cols=5, header_cols=33, sheet_title="some-other-tab",
        )
        self.assertEqual(widened, {})
        self.assertIsNone(payload)

    def test_seed_widens_before_writing_values(self):
        """Order matters: a write that precedes the widen is the bug itself."""
        order = []

        def fake_api(url, token, *, method="GET", data=None):
            if "values:batchUpdate" in url:
                order.append("values")
            elif url.endswith(":batchUpdate"):
                order.append("widen")
            elif method == "GET":
                order.append("read")
                return {"sheets": [{"properties": {
                    "sheetId": 7, "title": "transactions",
                    "gridProperties": {"columnCount": 33},
                }}]}
            return {}

        with mock.patch.object(bs, "api_request", side_effect=fake_api):
            bs.seed_tab_headers("tok", "sid", _specs(33))

        self.assertEqual(order, ["read", "widen", "values"])


if __name__ == "__main__":
    unittest.main()
