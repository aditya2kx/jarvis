#!/usr/bin/env python3
"""Tests for core.datastore.read_query access-error handling.

Regression guard for the 2026-06-03 BHAGA BQ incident: the orchestrator SA
lacked roles/bigquery.jobUser + roles/bigquery.dataEditor, so every BQ job
returned 403. `read_query` swallowed that into an empty list, which surfaced
downstream as a misleading `max() iterable argument is empty` crash in
`materialize_model_bq`. A permission/auth error must now PROPAGATE so the real
cause is visible; only genuinely-lenient errors (e.g. a not-yet-created table
during the migration bootstrap) keep degrading to [].
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import datastore


class _FakeClient:
    def __init__(self, exc: Exception):
        self._exc = exc

    def query(self, sql):  # noqa: D401 - mimics bigquery.Client.query
        raise self._exc


def _forbidden() -> Exception:
    try:
        from google.api_core import exceptions as gexc

        return gexc.Forbidden("Access Denied: User does not have bigquery.jobs.create")
    except Exception:  # google libs unavailable in the test env
        e = RuntimeError("403 Access Denied")
        e.code = 403  # type: ignore[attr-defined]
        return e


class ReadQueryAccessErrorTest(unittest.TestCase):
    def test_access_error_is_reraised(self):
        with mock.patch.object(datastore, "get_client", return_value=_FakeClient(_forbidden())):
            with self.assertRaises(Exception) as ctx:
                datastore.read_query("SELECT 1")
            self.assertTrue(datastore._is_access_error(ctx.exception))

    def test_generic_error_still_swallowed(self):
        client = _FakeClient(ValueError("table not found during migration bootstrap"))
        with mock.patch.object(datastore, "get_client", return_value=client):
            self.assertEqual(datastore.read_query("SELECT 1"), [])

    def test_no_client_returns_empty(self):
        with mock.patch.object(datastore, "get_client", return_value=None):
            self.assertEqual(datastore.read_query("SELECT 1"), [])

    def test_is_access_error_by_http_code(self):
        e401 = RuntimeError("unauthorized")
        e401.code = 401  # type: ignore[attr-defined]
        e403 = RuntimeError("forbidden")
        e403.code = 403  # type: ignore[attr-defined]
        self.assertTrue(datastore._is_access_error(e401))
        self.assertTrue(datastore._is_access_error(e403))
        self.assertFalse(datastore._is_access_error(ValueError("nope")))


if __name__ == "__main__":
    unittest.main()
