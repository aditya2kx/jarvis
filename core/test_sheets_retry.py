#!/usr/bin/env python3
"""Tests for core.sheets_retry — the shared Sheets-API backoff policy.

Covers retryability classification (HTTP code + RESOURCE_EXHAUSTED body),
the retry-then-succeed path, give-up-after-max-attempts, and that a
non-retryable status (e.g. 400) raises immediately without sleeping.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import sheets_retry


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://sheets.googleapis.com/x", code, "err", {}, io.BytesIO(body.encode())
    )


class IsRetryableTest(unittest.TestCase):
    def test_429_code_retryable(self):
        self.assertTrue(sheets_retry.is_retryable(429, ""))

    def test_5xx_retryable(self):
        for c in (500, 502, 503, 504):
            self.assertTrue(sheets_retry.is_retryable(c, ""))

    def test_resource_exhausted_body_retryable_even_if_code_odd(self):
        body = '{"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota"}}'
        # Even a 403-wrapped quota error (some proxies) is caught via the body.
        self.assertTrue(sheets_retry.is_retryable(403, body))

    def test_400_not_retryable(self):
        self.assertFalse(sheets_retry.is_retryable(400, '{"error": {"code": 400}}'))

    def test_non_json_body_not_retryable(self):
        self.assertFalse(sheets_retry.is_retryable(404, "<html>nope</html>"))


class RequestWithBackoffTest(unittest.TestCase):
    def setUp(self):
        self.sleeps: list[float] = []
        p = mock.patch.object(sheets_retry.time, "sleep", self.sleeps.append)
        p.start()
        self.addCleanup(p.stop)

    def test_retries_429_then_succeeds(self):
        attempts = {"n": 0}

        def do_call():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise _http_error(429, '{"error": {"status": "RESOURCE_EXHAUSTED"}}')
            return {"ok": True}

        out = sheets_retry.request_with_backoff(
            do_call, method="POST", url="u", base_delay=0.01,
        )
        self.assertEqual(out, {"ok": True})
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(len(self.sleeps), 2)  # slept before attempts 2 and 3

    def test_gives_up_after_max_attempts(self):
        def do_call():
            raise _http_error(429, '{"error": {"status": "RESOURCE_EXHAUSTED"}}')

        with self.assertRaises(RuntimeError) as ctx:
            sheets_retry.request_with_backoff(
                do_call, method="POST", url="u", max_attempts=4, base_delay=0.01,
            )
        self.assertIn("HTTP 429", str(ctx.exception))
        self.assertEqual(len(self.sleeps), 3)  # 4 attempts => 3 sleeps

    def test_non_retryable_raises_immediately(self):
        calls = {"n": 0}

        def do_call():
            calls["n"] += 1
            raise _http_error(400, '{"error": {"code": 400, "message": "bad"}}')

        with self.assertRaises(RuntimeError) as ctx:
            sheets_retry.request_with_backoff(do_call, method="GET", url="u")
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertEqual(calls["n"], 1)
        self.assertEqual(self.sleeps, [])  # no retry, no sleep

    def test_jitter_within_bounds(self):
        attempts = {"n": 0}

        def do_call():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _http_error(503, "")
            return {}

        sheets_retry.request_with_backoff(
            do_call, method="POST", url="u", base_delay=2.0,
        )
        # first backoff = base*2**0 + jitter(0..base) => in [2.0, 4.0)
        self.assertEqual(len(self.sleeps), 1)
        self.assertGreaterEqual(self.sleeps[0], 2.0)
        self.assertLess(self.sleeps[0], 4.0)


if __name__ == "__main__":
    unittest.main()
