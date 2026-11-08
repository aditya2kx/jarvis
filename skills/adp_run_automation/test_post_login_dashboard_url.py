"""Regression: post-login dashboard URL must accept main2 without trusting arbitrary ADP hosts.

Prod 2026-11-06 Sync ADP ``bhaga-daily-refresh-8trf8`` timed out waiting for
``runpayrollmain.adp.com/.*/v2/`` while already on
``https://runpayrollmain2.adp.com/@…/v2/`` and raised a false authfail.
"""
from __future__ import annotations

import unittest
from unittest import mock

from skills.adp_run_automation import runner as r


# Landing URL shape from the Nov 6 Cloud Run failure (tenant UUID fabricated).
NOV6_MAIN2_DASHBOARD = (
    "https://runpayrollmain2.adp.com/@00000000-0000-0000-0000-000000000000/v2/"
)
MAIN_DASHBOARD = "https://runpayrollmain.adp.com/@00000000-0000-0000-0000-000000000000/v2/"
MAIN_HASH_HOME = "https://runpayrollmain.adp.com/rpm/v2/#/home"


class IsAdpRunDashboardUrl(unittest.TestCase):
    def test_accepts_legacy_main_host(self):
        self.assertTrue(r.is_adp_run_dashboard_url(MAIN_DASHBOARD))

    def test_accepts_main2_nov6_incident_url(self):
        self.assertTrue(r.is_adp_run_dashboard_url(NOV6_MAIN2_DASHBOARD))
        self.assertTrue(r.POST_LOGIN_URL_RE.search(NOV6_MAIN2_DASHBOARD))

    def test_accepts_main_hash_home_path(self):
        self.assertTrue(r.is_adp_run_dashboard_url(MAIN_HASH_HOME))

    def test_accepts_future_mainN_digit_suffix(self):
        self.assertTrue(
            r.is_adp_run_dashboard_url(
                "https://runpayrollmain3.adp.com/@tenant/v2/"
            )
        )

    def test_rejects_signin_spa(self):
        self.assertFalse(
            r.is_adp_run_dashboard_url(
                "https://online.adp.com/signin/v1/?APPID=RUN&productId=80e309c3"
            )
        )

    def test_rejects_enrollment_host(self):
        self.assertFalse(
            r.is_adp_run_dashboard_url("https://runpayroll.adp.com/enrollment.aspx")
        )

    def test_rejects_auth_aspx_on_runpayroll(self):
        self.assertFalse(
            r.is_adp_run_dashboard_url(
                "https://runpayroll.adp.com/protected/auth.aspx?brand=x&auth=OLP"
            )
        )

    def test_rejects_federation_cmninit_even_on_main2(self):
        # Mid-redirect URL from the same Nov 6 nav chain — host is main2 but
        # path is not the v2 shell yet.
        mid = (
            "https://runpayrollmain2.adp.com/@00000000-0000-0000-0000-000000000000/"
            "registered/cmninit.aspx?IID=29808083&target=https://runpayrollmain2.adp.com/"
            "cmnMainFrame.aspx&ProvisioningProfileID=65988852&Stc=True"
        )
        self.assertFalse(r.is_adp_run_dashboard_url(mid))

    def test_rejects_sorry_throttle(self):
        self.assertFalse(r.is_adp_run_dashboard_url("https://sorry.adp.com/sorry/"))

    def test_rejects_arbitrary_adp_host(self):
        self.assertFalse(
            r.is_adp_run_dashboard_url("https://workforcenow.adp.com/theme/admin.html")
        )

    def test_rejects_main_host_without_v2_path(self):
        self.assertFalse(
            r.is_adp_run_dashboard_url(
                "https://runpayrollmain.adp.com/@00000000-0000-0000-0000-000000000000/"
            )
        )

    def test_rejects_empty(self):
        self.assertFalse(r.is_adp_run_dashboard_url(""))
        self.assertFalse(r.is_adp_run_dashboard_url(None))  # type: ignore[arg-type]


class RunDashboardUrl(unittest.TestCase):
    def test_keeps_main2_host(self):
        self.assertEqual(
            r.run_dashboard_url("https://runpayrollmain2.adp.com/@x/v2/#/home", "t-1"),
            "https://runpayrollmain2.adp.com/@t-1/v2/",
        )

    def test_keeps_main_host(self):
        self.assertEqual(
            r.run_dashboard_url(MAIN_HASH_HOME, "t-1"),
            "https://runpayrollmain.adp.com/@t-1/v2/",
        )

    def test_off_run_host_falls_back_to_canonical(self):
        for url in ("https://ngapps.adp.com/apps/run/errorPage",
                    "https://online.adp.com/signin/v1/", "", None):
            self.assertEqual(
                r.run_dashboard_url(url, "t-1"),  # type: ignore[arg-type]
                "https://runpayrollmain.adp.com/@t-1/v2/",
            )


class FakeLoginFormPage:
    """Page stub: URL + optional visible User ID / Password roles."""

    def __init__(self, url: str, *, visible_roles: tuple[str, ...] = ()):
        self.url = url
        self._visible = {n.lower() for n in visible_roles}

    def get_by_role(self, role, name=None):
        assert role == "textbox"
        pat = name.pattern if hasattr(name, "pattern") else str(name)
        loc = mock.MagicMock()

        def is_visible(timeout=0):
            for label in ("user id", "password"):
                if label in pat.lower() and label in self._visible:
                    return True
            raise TimeoutError("not visible")

        loc.first.is_visible = is_visible
        return loc


class AssertPostLoginDashboard(unittest.TestCase):
    def test_main2_without_login_form_passes(self):
        page = FakeLoginFormPage(NOV6_MAIN2_DASHBOARD)
        r._assert_post_login_dashboard(page, store="palmetto")

    def test_main2_with_password_field_raises(self):
        page = FakeLoginFormPage(NOV6_MAIN2_DASHBOARD, visible_roles=("password",))
        with mock.patch.object(
            r, "_raise_with_evidence", side_effect=RuntimeError("form")
        ) as rwe:
            with self.assertRaises(RuntimeError):
                r._assert_post_login_dashboard(page, store="palmetto")
        self.assertIn("sign-in form is still visible", rwe.call_args.kwargs["reason"])

    def test_cmninit_url_raises_not_dashboard(self):
        page = FakeLoginFormPage(
            "https://runpayrollmain2.adp.com/@t/registered/cmninit.aspx"
        )
        with mock.patch.object(
            r, "_raise_with_evidence", side_effect=RuntimeError("url")
        ) as rwe:
            with self.assertRaises(RuntimeError):
                r._assert_post_login_dashboard(page, store="palmetto")
        self.assertIn("did not reach dashboard", rwe.call_args.kwargs["reason"])


class ConfirmDashboardMain2(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(
            r, "_load_store_profile", return_value={"adp_run": {"tenant_uuid": "t-1"}}
        )
        p.start()
        self.addCleanup(p.stop)

    def test_main2_dashboard_is_left_alone(self):
        from skills.adp_run_automation.test_select_company import LandingPage

        page = LandingPage(NOV6_MAIN2_DASHBOARD)
        with mock.patch.object(r, "_login_form_still_visible", return_value=False):
            r._confirm_dashboard(page, store="palmetto")
        self.assertEqual(page.gotos, [])

    def test_error_page_reopen_accepts_main2_landing(self):
        from skills.adp_run_automation.test_select_company import LandingPage

        page = LandingPage(
            "https://ngapps.adp.com/apps/run/errorPage",
            after_goto=NOV6_MAIN2_DASHBOARD,
        )
        with mock.patch.object(r, "_login_form_still_visible", return_value=False):
            r._confirm_dashboard(page, store="palmetto")
        self.assertEqual(
            page.gotos, ["https://runpayrollmain.adp.com/@t-1/v2/"]
        )
        self.assertEqual(page.url, NOV6_MAIN2_DASHBOARD)


if __name__ == "__main__":
    unittest.main()
