"""Multi-company ADP logins land on a Companies list — pick the store's company by client ID."""
from __future__ import annotations

import unittest
from unittest import mock

from skills.adp_run_automation import runner as r


class FakePage:
    def __init__(self, reads):
        self.reads, self.url = list(reads), "https://runpayrollmain.adp.com/@x/v2/"
        self.clicked = mock.MagicMock()

    def evaluate(self, _js, _iid):
        return self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]

    def wait_for_timeout(self, _ms):
        pass

    def wait_for_load_state(self, _state):
        pass

    def locator(self, sel):
        assert sel == "[data-jarvis-company='1']"
        loc = mock.MagicMock()
        loc.first.click = self.clicked
        return loc


PROFILE = {"adp_run": {"iid": "30109821"}}


class SelectCompany(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(r, "_load_store_profile", return_value=PROFILE)
        p.start()
        self.addCleanup(p.stop)

    def test_single_company_dashboard_is_left_alone(self):
        page = FakePage([{"list": False}])
        r._select_company(page, store="palmetto", wait_s=0)
        page.clicked.assert_not_called()

    def test_company_list_opens_the_matching_client_id(self):
        page = FakePage([{"list": True, "rows": 1, "link": True}, {"list": False}])
        r._select_company(page, store="palmetto")
        page.clicked.assert_called_once()

    def test_no_unique_match_raises_with_evidence(self):
        page = FakePage([{"list": True, "rows": 0, "link": False}])
        with mock.patch.object(r, "_raise_with_evidence", side_effect=RuntimeError("no row")) as rwe:
            with self.assertRaises(RuntimeError):
                r._select_company(page, store="palmetto")
        self.assertIn("30109821", rwe.call_args.kwargs["reason"])
        page.clicked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
