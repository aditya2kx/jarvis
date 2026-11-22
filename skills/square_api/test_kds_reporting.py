"""KDS Reporting API queries are scoped to one store's Square location."""

from __future__ import annotations

import unittest
from unittest import mock

from skills.square_api import kds_reporting

_VIEW = {
    "name": "KDS",
    "dimensions": [{"name": "KDS.ticket_name"}, {"name": "KDS.display_on_kds_at"}],
    "measures": [{"name": "KDS.ticket_count"}],
}


class LocationFilter(unittest.TestCase):
    def test_query_filters_on_location(self):
        with mock.patch.object(kds_reporting, "_reporting_request", return_value={"data": []}) as req:
            kds_reporting._query_kds_view(
                token="t", kds_view=_VIEW, begin_time="b", end_time="e", location_id="L6AQ95CN5NG4W",
            )
        query = req.call_args.kwargs["body"]["query"]
        self.assertEqual(
            query["filters"],
            [{"member": "KDS.location_id", "operator": "equals", "values": ["L6AQ95CN5NG4W"]}],
        )

    def test_each_store_resolves_its_own_location(self):
        self.assertEqual(kds_reporting._location_id("palmetto"), "L9DDZF3CQS8AK")
        self.assertEqual(kds_reporting._location_id("houston"), "L6AQ95CN5NG4W")

    def test_missing_location_refuses_merchant_wide_query(self):
        with mock.patch(
            "agents.bhaga.scripts.backfill_bigquery.load_store_profile", return_value={"square": {}},
        ):
            with self.assertRaisesRegex(RuntimeError, "no square.location_id"):
                kds_reporting._location_id("nowhere")


if __name__ == "__main__":
    unittest.main()
