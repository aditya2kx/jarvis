#!/usr/bin/env python3
"""Unit tests for ADP's "verify/add your mobile number" interstitial skip.

Background — 2026-06-06 sandbox backfill
========================================
ADP RUN began inserting a contact-channel-collection page (custom
``<sdf-phone-number-input>`` + "Verify mobile number" / "Remind me later",
ids ``channel-collection-save`` / ``channel-collection-remind``) between the
password step and the OTP challenge. It has no code-entry box, so the OTP
handler landed there and raised "ADP 2FA code-entry input not found. Selector
drift." The fix (`_dismiss_adp_channel_collection`) clicks "Remind me later" to
skip it before the OTP flow runs.

These tests use a fake page emulating the slice of the Playwright locator API
the helper touches (no browser).

Run:
    python3 -m unittest skills.adp_run_automation.test_runner_channel_collection -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.adp_run_automation.runner import _dismiss_adp_channel_collection


class _FakeLoc:
    def __init__(self, *, visible: bool, clickable: bool = True, on_click=None):
        self._visible = visible
        self._clickable = clickable
        self._on_click = on_click

    @property
    def first(self):
        return self

    def wait_for(self, *, state="visible", timeout=0):
        if not self._visible:
            raise TimeoutError("not visible")

    def click(self, *, timeout=0):
        if not self._clickable:
            raise TimeoutError("not clickable")
        if self._on_click:
            self._on_click()


class _FakePage:
    def __init__(self, *, locators=None, remind_role_loc=None, url="https://online.adp.com/x"):
        self._locators = locators or {}
        # Locator returned by get_by_role("button", name=/remind me later/); a
        # not-visible/not-clickable default means "no such control".
        self._remind_role_loc = remind_role_loc or _FakeLoc(visible=False, clickable=False)
        self.url = url

    def locator(self, selector):
        return self._locators.get(selector) or _FakeLoc(visible=False, clickable=False)

    def get_by_role(self, role, *, name=None):
        if role == "button" and name is not None and name.search("Remind me later"):
            return self._remind_role_loc
        return _FakeLoc(visible=False, clickable=False)

    def wait_for_timeout(self, ms):
        pass


class TestDismissChannelCollection(unittest.TestCase):
    def test_absent_returns_false(self):
        page = _FakePage()  # no interstitial elements visible
        self.assertFalse(_dismiss_adp_channel_collection(page))

    def test_present_clicks_remind_and_returns_true(self):
        clicked = []
        remind = _FakeLoc(visible=True, on_click=lambda: clicked.append("remind"))
        page = _FakePage(locators={"#channel-collection-remind": remind})
        self.assertTrue(_dismiss_adp_channel_collection(page))
        self.assertEqual(clicked, ["remind"])

    def test_detected_via_phone_input_then_clicks_remind(self):
        clicked = []
        page = _FakePage(locators={
            "#phone-number-input": _FakeLoc(visible=True),
            "#channel-collection-remind": _FakeLoc(visible=True, on_click=lambda: clicked.append("r")),
        })
        self.assertTrue(_dismiss_adp_channel_collection(page))
        self.assertEqual(clicked, ["r"])

    def test_present_but_unclickable_returns_false(self):
        page = _FakePage(locators={
            "#channel-collection-remind": _FakeLoc(visible=True, clickable=False),
        })
        # Detected but cannot dismiss → False (caller's OTP flow raises evidence).
        self.assertFalse(_dismiss_adp_channel_collection(page))


if __name__ == "__main__":
    unittest.main()
