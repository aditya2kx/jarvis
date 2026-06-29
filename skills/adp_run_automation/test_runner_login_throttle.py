#!/usr/bin/env python3
"""Regression tests for _wait_for_login_form ADP throttle recovery.

Covers the 2026-06-28 incident: ADP retired the bare runpayroll.adp.com
entry point, which now server-redirects to https://sorry.adp.com/sorry/.
The root-cause fix points LOGIN_URL at runpayroll.adp.com/enrollment.aspx
(ADP's federation redirector → live sign-in SPA). These tests cover the
complementary resilience net: if a future goto still lands on sorry.adp.com,
the recovery must use a fresh goto(LOGIN_URL) (NOT page.reload(), which
re-requests the current sorry URL and can never escape) + exponential backoff,
and raise AdpLoginThrottled (graceful skip) rather than hard-failing.

Three scenarios:

1. Throttle then recovery: first attempt lands on sorry.adp.com; a later
   attempt (after retry-goto) renders the login form → returns UID box.
   Assert: goto(LOGIN_URL) is called (not reload), and the UID locator is
   returned successfully.

2. Persistent throttle: all attempts land on sorry.adp.com → AdpLoginThrottled.
   Assert: the typed throttle exception is raised (not RuntimeError).

3. Non-throttle stall: page.url is the real login SPA but the form never
   renders → RuntimeError (unchanged behaviour from before the fix).

Run:
    python3 -m unittest skills.adp_run_automation.test_runner_login_throttle -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from agents.bhaga.scripts.otp_gate import AdpLoginThrottled
from skills.adp_run_automation.runner import LOGIN_URL, _wait_for_login_form


# ---------------------------------------------------------------------------
# Fake Playwright objects
# ---------------------------------------------------------------------------

class _FakeLocator:
    """Locator stub whose wait_for() result is controlled per-call."""

    def __init__(self, visible_on_attempt: int | None = None):
        """
        If ``visible_on_attempt`` is N, wait_for() succeeds on attempt N and
        raises on all earlier calls; if None it always raises (never visible).
        """
        self._visible_on = visible_on_attempt
        self._calls = 0
        self._is_first = True

    @property
    def first(self):
        return self

    def wait_for(self, *, state, timeout):
        self._calls += 1
        if self._visible_on is not None and self._calls >= self._visible_on:
            return
        raise Exception("locator not visible (fake timeout)")


class _FakePage:
    """Playwright Page stub for _wait_for_login_form.

    ``url_sequence``: list of URLs the page.url property returns per access,
    cycling through. Use this to simulate the sorry-page redirect and then
    recovery after a fresh goto.
    ``locator_stub``: the _FakeLocator returned by get_by_role.
    """

    def __init__(self, url_sequence: list[str], locator_stub: _FakeLocator):
        self._url_seq = url_sequence
        self._url_idx = 0
        self._locator = locator_stub
        self.goto_calls: list[str] = []
        self.reload_calls: int = 0

    @property
    def url(self) -> str:
        # Advance through the sequence; stay at the last entry once exhausted.
        idx = min(self._url_idx, len(self._url_seq) - 1)
        return self._url_seq[idx]

    def _advance_url(self):
        if self._url_idx < len(self._url_seq) - 1:
            self._url_idx += 1

    def goto(self, url: str, *, wait_until=None, timeout=None):
        self.goto_calls.append(url)
        self._advance_url()

    def reload(self, *, wait_until=None, timeout=None):
        self.reload_calls += 1
        # reload() does NOT advance the URL — it stays on whatever page we're on,
        # which is the sorry page. This is intentional: it simulates the bug where
        # reload gets stuck on sorry.adp.com.

    def wait_for_load_state(self, state, *, timeout=None):
        pass

    def get_by_role(self, role, *, name=None):
        return self._locator


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWaitForLoginFormThrottleRecovery(unittest.TestCase):

    def _no_sleep(self, seconds):
        """Drop-in for time.sleep that returns immediately."""

    def test_throttle_then_recovery(self):
        """first goto lands on sorry; retry-goto navigates back; form renders."""
        # URL sequence: sorry on first access (initial page after _load_login_page),
        # then login SPA once our retry goto fires, then login SPA again for the
        # url read in the final retry-settled log.
        url_seq = [
            "https://sorry.adp.com/sorry/",  # first wait-uid-box sees this
            "https://online.adp.com/signin/v1/?APPID=RUN",  # after goto(LOGIN_URL) on retry
            "https://online.adp.com/signin/v1/?APPID=RUN",  # retry-settled log
        ]
        # UID box becomes visible on the 2nd call (attempt 2 after retry goto).
        locator = _FakeLocator(visible_on_attempt=2)
        page = _FakePage(url_seq, locator)

        result = _wait_for_login_form(page, max_retries=2, _sleep_fn=self._no_sleep)

        self.assertIs(result, locator)
        # Must have issued a fresh goto (not reload) to escape the sorry page.
        self.assertIn(LOGIN_URL, page.goto_calls,
                      "recovery must call goto(LOGIN_URL), not reload()")
        self.assertEqual(page.reload_calls, 0,
                         "reload() must never be called — it stays stuck on sorry.adp.com")

    def test_persistent_throttle_raises_adp_login_throttled(self):
        """All attempts stay on sorry.adp.com → AdpLoginThrottled (not RuntimeError)."""
        sorry_url = "https://sorry.adp.com/sorry/"
        url_seq = [sorry_url] * 10  # always sorry
        locator = _FakeLocator(visible_on_attempt=None)  # never visible
        page = _FakePage(url_seq, locator)

        with self.assertRaises(AdpLoginThrottled) as ctx:
            _wait_for_login_form(page, max_retries=2, _sleep_fn=self._no_sleep)

        msg = str(ctx.exception)
        self.assertIn("sorry.adp.com", msg)
        self.assertIn("3", msg)  # total_attempts = max_retries + 1

    def test_persistent_throttle_does_not_raise_runtime_error(self):
        """AdpLoginThrottled is not a RuntimeError — callers distinguish them."""
        sorry_url = "https://sorry.adp.com/sorry/"
        page = _FakePage([sorry_url] * 10, _FakeLocator(visible_on_attempt=None))

        with self.assertRaises(AdpLoginThrottled):
            _wait_for_login_form(page, max_retries=2, _sleep_fn=self._no_sleep)

        # Confirm it is NOT a plain RuntimeError (different handling path).
        try:
            _wait_for_login_form(page, max_retries=2, _sleep_fn=self._no_sleep)
        except AdpLoginThrottled:
            pass
        except RuntimeError:
            self.fail("Persistent throttle must raise AdpLoginThrottled, not RuntimeError")

    def test_non_throttle_stall_raises_runtime_error(self):
        """Form stalls on real login SPA (not sorry page) → RuntimeError unchanged."""
        login_url = "https://online.adp.com/signin/v1/?APPID=RUN"
        url_seq = [login_url] * 10  # real login page but form never renders
        locator = _FakeLocator(visible_on_attempt=None)  # never visible
        page = _FakePage(url_seq, locator)

        with self.assertRaises(RuntimeError):
            _wait_for_login_form(page, max_retries=2, _sleep_fn=self._no_sleep)

    def test_non_throttle_stall_does_not_raise_adp_login_throttled(self):
        """A JS-hydration stall on the real SPA must not be misclassified as throttle."""
        login_url = "https://online.adp.com/signin/v1/?APPID=RUN"
        page = _FakePage([login_url] * 10, _FakeLocator(visible_on_attempt=None))

        try:
            _wait_for_login_form(page, max_retries=2, _sleep_fn=self._no_sleep)
        except AdpLoginThrottled:
            self.fail("Non-throttle stall must not raise AdpLoginThrottled")
        except RuntimeError:
            pass

    def test_no_retry_needed_returns_uid_box(self):
        """Login form renders on first attempt — no retry, no goto, no reload."""
        page = _FakePage(
            ["https://online.adp.com/signin/v1/?APPID=RUN"],
            _FakeLocator(visible_on_attempt=1),
        )
        result = _wait_for_login_form(page, max_retries=2, _sleep_fn=self._no_sleep)
        self.assertIsNotNone(result)
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(page.reload_calls, 0)


class _FakeEvidencePage:
    """Minimal page stub for _raise_with_evidence (screenshot must not fail)."""

    def __init__(self, url: str):
        self._url = url

    @property
    def url(self) -> str:
        return self._url

    def screenshot(self, *, path=None, full_page=None):
        # Writing under /Users/.../ .bhaga in CI would fail; raising here is fine
        # because _raise_with_evidence swallows screenshot errors by design.
        raise OSError("no screenshot dir in test")


class TestPostLoginSorryGracefulSkip(unittest.TestCase):
    """_raise_with_evidence must raise the typed exception passed via exc_factory.

    This is the mechanism behind the post-login sorry.adp.com graceful skip in
    _ensure_logged_in: when ADP redirects to sorry.adp.com *after* a valid login
    (RUN maintenance window / post-auth throttle), it raises AdpLoginThrottled
    (→ daily_refresh graceful skip) instead of a hard RuntimeError.
    """

    def test_exc_factory_raises_adp_login_throttled(self):
        from skills.adp_run_automation.runner import _raise_with_evidence

        page = _FakeEvidencePage("https://sorry.adp.com/sorry/")
        with self.assertRaises(AdpLoginThrottled):
            _raise_with_evidence(
                page, store="palmetto",
                reason="post-login sorry redirect",
                exc_factory=AdpLoginThrottled,
            )

    def test_exc_factory_default_is_runtime_error(self):
        from skills.adp_run_automation.runner import _raise_with_evidence

        page = _FakeEvidencePage("https://online.adp.com/somewhere")
        with self.assertRaises(RuntimeError) as ctx:
            _raise_with_evidence(page, store="palmetto", reason="generic auth fail")
        # Default path must NOT be AdpLoginThrottled (different handling).
        self.assertNotIsInstance(ctx.exception, AdpLoginThrottled)


if __name__ == "__main__":
    unittest.main()
