"""Unit tests for scripts/secret_manager_put.py — mocked Secret Manager."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import secret_manager_put as smp  # noqa: E402


class ReadPayloadTests(unittest.TestCase):
    def test_from_env(self):
        os.environ["JARVIS_TEST_SECRET_PAYLOAD"] = "abc123"
        try:
            self.assertEqual(
                smp._read_payload(None, "JARVIS_TEST_SECRET_PAYLOAD"),
                b"abc123",
            )
        finally:
            del os.environ["JARVIS_TEST_SECRET_PAYLOAD"]

    def test_strips_single_trailing_newline(self):
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(b"token-value\n")
            path = fh.name
        try:
            self.assertEqual(smp._read_payload(path, None), b"token-value")
        finally:
            os.unlink(path)

    def test_rejects_both_sources(self):
        with self.assertRaises(SystemExit):
            smp._read_payload("x", "Y")


class PutVersionTests(unittest.TestCase):
    def test_dry_run_skips_add(self):
        sm = MagicMock()
        sm.get_secret.return_value = MagicMock()
        smp.put_secret_version(
            project="jarvis-bhaga-prod",
            secret_name="tesla-fleet-client-id",
            payload=b"not-printed",
            dry_run=True,
            sm=sm,
        )
        sm.add_secret_version.assert_not_called()

    def test_add_version_when_secret_exists(self):
        sm = MagicMock()
        sm.get_secret.return_value = MagicMock()
        smp.put_secret_version(
            project="jarvis-bhaga-prod",
            secret_name="tesla-fleet-client-id",
            payload=b"not-printed",
            dry_run=False,
            sm=sm,
        )
        sm.add_secret_version.assert_called_once()
        kwargs = sm.add_secret_version.call_args.kwargs
        self.assertEqual(
            kwargs["parent"],
            "projects/jarvis-bhaga-prod/secrets/tesla-fleet-client-id",
        )
        self.assertEqual(kwargs["payload"]["data"], b"not-printed")


if __name__ == "__main__":
    unittest.main()
