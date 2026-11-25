"""Unit tests for ``_await_v2_shell`` — blank SPA recovery after dashboard goto."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.adp_run_automation import runner as r


class _FakeLocator:
    def __init__(self, fails_before: int = 0):
        self.fails_before = fails_before
        self.waits = 0

    @property
    def first(self):
        return self

    def wait_for(self, *, state, timeout):
        self.waits += 1
        if self.waits <= self.fails_before:
            raise TimeoutError(f"fake blank shell wait #{self.waits}")


class AwaitV2ShellTests(unittest.TestCase):
    def test_ready_on_first_try(self):
        page = MagicMock()
        page.url = "https://runpayrollmain2.adp.com/@x/v2/"
        loc = _FakeLocator(fails_before=0)
        page.locator.return_value = loc
        r._await_v2_shell(page, tag="t", max_retries=2)
        self.assertEqual(loc.waits, 1)
        page.reload.assert_not_called()

    def test_reloads_after_blank_then_succeeds(self):
        page = MagicMock()
        page.url = "https://runpayrollmain2.adp.com/@x/v2/"
        loc = _FakeLocator(fails_before=1)
        page.locator.return_value = loc
        r._await_v2_shell(page, tag="t", max_retries=2)
        self.assertEqual(loc.waits, 2)
        page.reload.assert_called_once()

    def test_raises_after_retries_exhausted(self):
        page = MagicMock()
        page.url = "https://runpayrollmain2.adp.com/@x/v2/"
        loc = _FakeLocator(fails_before=99)
        page.locator.return_value = loc
        with patch.object(r, "_save_evidence", return_value="shot.png"):
            with self.assertRaises(TimeoutError) as cm:
                r._await_v2_shell(page, tag="t", max_retries=1)
        self.assertIn("v2 shell never painted", str(cm.exception))
        self.assertEqual(page.reload.call_count, 1)


if __name__ == "__main__":
    unittest.main()
