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

    def test_waits_for_client_ids_to_render(self):
        page = FakePage([{"list": True, "rows": 0, "link": False},
                         {"list": True, "rows": 1, "link": True}, {"list": False}])
        r._select_company(page, store="palmetto")
        page.clicked.assert_called_once()

    def test_no_unique_match_raises_with_evidence(self):
        page = FakePage([{"list": True, "rows": 0, "link": False}])
        with mock.patch.object(r, "_raise_with_evidence", side_effect=RuntimeError("no row")) as rwe:
            with self.assertRaises(RuntimeError):
                r._select_company(page, store="palmetto")
        self.assertIn("30109821", rwe.call_args.kwargs["reason"])
        page.clicked.assert_not_called()


class LandingPage:
    """A page that ADP leaves on ``url``; ``goto`` lands on ``after_goto``."""

    def __init__(self, url, after_goto=None):
        self.url, self.after_goto, self.gotos = url, after_goto, []

    def goto(self, url, **_kw):
        self.gotos.append(url)
        if self.after_goto:
            self.url = self.after_goto

    def wait_for_url(self, pattern, timeout=0):
        if not pattern.search(self.url):
            raise TimeoutError(self.url)


ERROR_PAGE = "https://ngapps.adp.com/apps/run/errorPage"
DASHBOARD = "https://runpayrollmain.adp.com/@t-1/v2/"


class ConfirmDashboard(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(r, "_load_store_profile",
                              return_value={"adp_run": {"tenant_uuid": "t-1"}})
        p.start()
        self.addCleanup(p.stop)

    def test_dashboard_is_left_alone(self):
        page = LandingPage(DASHBOARD)
        r._confirm_dashboard(page, store="palmetto")
        self.assertEqual(page.gotos, [])

    def test_error_page_reopens_the_tenant_dashboard(self):
        page = LandingPage(ERROR_PAGE, after_goto=DASHBOARD)
        r._confirm_dashboard(page, store="palmetto")
        self.assertEqual(page.gotos, [DASHBOARD])
        self.assertEqual(page.url, DASHBOARD)

    def test_still_rejected_raises_once_with_the_url(self):
        page = LandingPage(ERROR_PAGE)
        with mock.patch.object(r, "_raise_with_evidence", side_effect=RuntimeError("off")) as rwe:
            with self.assertRaises(RuntimeError):
                r._confirm_dashboard(page, store="palmetto")
        self.assertEqual(page.gotos, [DASHBOARD])
        self.assertIn(ERROR_PAGE, rwe.call_args.kwargs["reason"])


if __name__ == "__main__":
    unittest.main()
