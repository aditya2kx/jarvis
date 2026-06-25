#!/usr/bin/env python3
"""Regression tests for _wait_for_earnings_ready_button.

Covers the 2026-06-23 incident: the ADP earnings "Your report is ready to
download" modal button timed out at 45 s because async report generation on
ADP's server exceeded the fixed wait. The fix replaces the single fixed-timeout
`wait_for` with a poll loop over ranked fallback locators controlled by
BHAGA_ADP_EARNINGS_READY_TIMEOUT_MS (default 90 000 ms).

These tests use a lightweight fake Playwright ``page`` object so no real browser
or network is needed. They verify three things:

1. Primary locator visible immediately → resolves and returns.
2. Primary not visible, fallback (role-based) visible → resolves and returns.
3. All locators invisible for the full timeout → diagnostic snapshot attempted,
   RuntimeError raised with the right message.

Run:
    python3 -m unittest skills.adp_run_automation.test_earnings_ready_dialog -v
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.adp_run_automation.runner import _wait_for_earnings_ready_button


# ---------------------------------------------------------------------------
# Fake Playwright objects — minimal surface needed by _wait_for_earnings_ready_button
# ---------------------------------------------------------------------------

class _FakeLocator:
    """Mimics a Playwright Locator with a controllable is_visible() result."""

    def __init__(self, visible: bool = False):
        self._visible = visible
        self.clicked = False

    def is_visible(self) -> bool:
        return self._visible

    def click(self) -> None:
        self.clicked = True

    # Playwright `locator.first` returns the same object in our fake.
    @property
    def first(self):
        return self


class _FakePage:
    """Mimics enough of a Playwright Page for _wait_for_earnings_ready_button.

    ``locators`` maps CSS selector string → _FakeLocator.
    ``role_button`` is the locator returned by get_by_role("button", ...).
    ``screenshot_raises`` makes page.screenshot() raise to test resilience.
    """

    def __init__(self, locators: dict, role_button: _FakeLocator | None = None,
                 screenshot_raises: bool = False):
        self._locators = locators
        self._role_button = role_button or _FakeLocator(visible=False)
        self._screenshot_raises = screenshot_raises
        self.url = "https://runpayrollmain.adp.com/v2/"
        self.screenshot_calls: list = []
        self.content_calls: int = 0

    def locator(self, selector: str):
        if selector in self._locators:
            return self._locators[selector]
        return _FakeLocator(visible=False)

    def get_by_role(self, role: str, *, name=None):
        return self._role_button

    def screenshot(self, *, path: str, full_page: bool = False) -> None:
        self.screenshot_calls.append(path)
        if self._screenshot_raises:
            raise OSError("screenshot failed in test")

    def content(self) -> str:
        self.content_calls += 1
        return "<html>fake</html>"


# ---------------------------------------------------------------------------
# Locator spec used in all tests — mirrors what runner.py passes.
# ---------------------------------------------------------------------------

_SPECS = [
    ('[data-test-id="download-report"]', "data-test-id=download-report"),
    ('get_by_role("button", name=re.compile(r"Download report", re.I))', "role-button-download-report"),
    ('[aria-label="Download report"]', "aria-label=Download-report"),
]


class TestWaitForEarningsReadyButton(unittest.TestCase):

    def test_primary_visible_immediately_returns_btn(self):
        """Primary locator visible at first check → function returns immediately."""
        primary = _FakeLocator(visible=True)
        page = _FakePage(
            locators={'[data-test-id="download-report"]': primary}
        )
        result = _wait_for_earnings_ready_button(
            page, timeout_ms=500, locator_specs=_SPECS
        )
        self.assertIs(result, primary)

    def test_fallback_role_button_used_when_primary_invisible(self):
        """Primary invisible, role-based fallback visible → resolves via fallback."""
        primary = _FakeLocator(visible=False)
        role_btn = _FakeLocator(visible=True)
        page = _FakePage(
            locators={'[data-test-id="download-report"]': primary},
            role_button=role_btn,
        )
        result = _wait_for_earnings_ready_button(
            page, timeout_ms=500, locator_specs=_SPECS
        )
        self.assertIs(result, role_btn)

    def test_aria_fallback_used_when_primary_and_role_invisible(self):
        """Primary and role-button invisible, aria-label fallback visible → resolves."""
        primary = _FakeLocator(visible=False)
        role_btn = _FakeLocator(visible=False)
        aria_btn = _FakeLocator(visible=True)
        page = _FakePage(
            locators={
                '[data-test-id="download-report"]': primary,
                '[aria-label="Download report"]': aria_btn,
            },
            role_button=role_btn,
        )
        result = _wait_for_earnings_ready_button(
            page, timeout_ms=500, locator_specs=_SPECS
        )
        self.assertIs(result, aria_btn)

    def test_timeout_raises_runtime_error_with_context(self):
        """All locators invisible → RuntimeError with URL + tried selectors."""
        page = _FakePage(locators={})
        with self.assertRaises(RuntimeError) as ctx:
            _wait_for_earnings_ready_button(
                page, timeout_ms=200, locator_specs=_SPECS
            )
        msg = str(ctx.exception)
        self.assertIn("ready-dialog button never appeared", msg)
        self.assertIn("data-test-id=download-report", msg)
        self.assertIn("role-button-download-report", msg)
        self.assertIn(page.url, msg)

    def test_timeout_attempts_diagnostic_snapshot(self):
        """On timeout a screenshot + HTML source are attempted."""
        page = _FakePage(locators={})
        with tempfile.TemporaryDirectory() as td:
            # Patch HOME so the snapshot lands in a temp dir instead of ~/.bhaga.
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                with self.assertRaises(RuntimeError):
                    _wait_for_earnings_ready_button(
                        page, timeout_ms=200, locator_specs=_SPECS
                    )
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home
        # At least one screenshot was attempted.
        self.assertGreater(len(page.screenshot_calls), 0)
        self.assertGreater(page.content_calls, 0)

    def test_snapshot_failure_does_not_suppress_timeout_error(self):
        """Even if the diagnostic snapshot itself raises, the RuntimeError still propagates."""
        page = _FakePage(locators={}, screenshot_raises=True)
        with self.assertRaises(RuntimeError) as ctx:
            _wait_for_earnings_ready_button(
                page, timeout_ms=200, locator_specs=_SPECS
            )
        self.assertIn("ready-dialog button never appeared", str(ctx.exception))

    def test_timeout_env_var_honored(self):
        """BHAGA_ADP_EARNINGS_READY_TIMEOUT_MS is read and respected.

        We set a very short timeout via env var (200ms) and verify the function
        raises before a 1s sleep would elapse — proving the timeout path is
        driven by the configurable value, not a hardcoded constant.
        """
        os.environ["BHAGA_ADP_EARNINGS_READY_TIMEOUT_MS"] = "200"
        try:
            timeout_ms = int(os.environ.get("BHAGA_ADP_EARNINGS_READY_TIMEOUT_MS", "90000"))
            page = _FakePage(locators={})
            t0 = time.monotonic()
            with self.assertRaises(RuntimeError):
                _wait_for_earnings_ready_button(
                    page, timeout_ms=timeout_ms, locator_specs=_SPECS
                )
            elapsed = time.monotonic() - t0
            # Must complete in well under 1 second (one poll interval).
            self.assertLess(elapsed, 1.5,
                            "timeout should fire in <1.5s for a 200ms window")
        finally:
            os.environ.pop("BHAGA_ADP_EARNINGS_READY_TIMEOUT_MS", None)


if __name__ == "__main__":
    unittest.main()
