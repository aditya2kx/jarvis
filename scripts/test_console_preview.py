"""Unit tests for scripts/console_preview.py — no browser, no network."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import console_preview as P  # noqa: E402


class FakePage:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.gotos = 0
        self.waits = []

    def goto(self, url, wait_until=None):
        self.gotos += 1
        return SimpleNamespace(status=self.statuses.pop(0))

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


class GotoThroughIapTests(unittest.TestCase):
    def test_403_while_grant_propagates_then_200(self):
        page = FakePage([403, 403, 200])
        self.assertEqual(P.goto_through_iap(page, "u").status, 200)
        self.assertEqual((page.gotos, page.waits), (3, [15_000, 15_000]))

    def test_first_200_does_not_wait(self):
        page = FakePage([200])
        self.assertEqual(P.goto_through_iap(page, "u").status, 200)
        self.assertEqual(page.waits, [])

    def test_persistent_403_gives_up_after_retries(self):
        page = FakePage([403] * (P.IAP_GRANT_RETRIES + 1))
        self.assertEqual(P.goto_through_iap(page, "u").status, 403)
        self.assertEqual(page.gotos, P.IAP_GRANT_RETRIES + 1)

    def test_other_errors_are_not_retried(self):
        page = FakePage([500])
        self.assertEqual(P.goto_through_iap(page, "u").status, 500)
        self.assertEqual(page.gotos, 1)


if __name__ == "__main__":
    unittest.main()
