"""Unit tests for scripts/cloud_agent_secret.py — no socket, no network."""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cloud_agent_secret as S  # noqa: E402


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FlowTests(unittest.TestCase):
    def test_sts_exchange_sends_cursor_jwt_for_the_pool_audience(self):
        seen = {}

        def fake_urlopen(req, timeout=0):
            seen["url"] = req.full_url
            seen["fields"] = dict(urllib.parse.parse_qsl(req.data.decode()))
            return _Resp(json.dumps({"access_token": "fed"}).encode())

        with mock.patch.object(S.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.assertEqual(S.federated_access_token("jwt"), "fed")
        self.assertEqual(seen["url"], "https://sts.googleapis.com/v1/token")
        self.assertEqual(seen["fields"]["subject_token"], "jwt")
        self.assertEqual(seen["fields"]["audience"], S.AUDIENCE)
        self.assertEqual(seen["fields"]["subject_token_type"], "urn:ietf:params:oauth:token-type:jwt")

    def test_access_secret_decodes_latest_version(self):
        payload = json.dumps({"payload": {"data": base64.b64encode(b"ghp_x").decode()}}).encode()
        with mock.patch.object(S.urllib.request, "urlopen", return_value=_Resp(payload)) as op:
            self.assertEqual(S.access_secret("github-bot-pat", "fed"), "ghp_x")
        req = op.call_args.args[0]
        self.assertTrue(req.full_url.endswith("/projects/jarvis-bhaga-prod/secrets/github-bot-pat/versions/latest:access"))
        self.assertEqual(req.get_header("Authorization"), "Bearer fed")

    def test_failure_is_one_line_on_stderr_and_nothing_on_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(S, "cursor_oidc_token", side_effect=OSError("no socket")), \
             mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            self.assertEqual(S.main(["github-bot-pat"]), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("github-bot-pat unavailable — no socket", err.getvalue())


if __name__ == "__main__":
    unittest.main()
