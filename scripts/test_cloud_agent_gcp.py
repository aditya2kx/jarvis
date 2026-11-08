"""Unit tests for scripts/cloud_agent_gcp.py — no socket, no network."""
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

import cloud_agent_gcp as G  # noqa: E402


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake(responses: dict, seen: list):
    def urlopen(req, timeout=0):
        seen.append(req)
        for suffix, body in responses.items():
            if req.full_url.endswith(suffix):
                return _Resp(json.dumps(body).encode())
        raise AssertionError(req.full_url)
    return urlopen


class FlowTests(unittest.TestCase):
    def test_sts_exchange_sends_cursor_jwt_for_the_pool_audience(self):
        seen: list = []
        with mock.patch.object(G.urllib.request, "urlopen", side_effect=_fake({"/v1/token": {"access_token": "fed"}}, seen)):
            self.assertEqual(G.federated_access_token("jwt"), "fed")
        fields = dict(urllib.parse.parse_qsl(seen[0].data.decode()))
        self.assertEqual(seen[0].full_url, "https://sts.googleapis.com/v1/token")
        self.assertEqual((fields["subject_token"], fields["audience"]), ("jwt", G.AUDIENCE))

    def test_secret_goes_through_the_agent_service_account(self):
        seen: list = []
        responses = {
            ":generateAccessToken": {"accessToken": "sa"},
            "/secrets/github-bot-pat/versions/latest:access": {"payload": {"data": base64.b64encode(b"ghp_x").decode()}},
        }
        with mock.patch.object(G.urllib.request, "urlopen", side_effect=_fake(responses, seen)):
            self.assertEqual(G.access_secret("github-bot-pat", G.sa_access_token("fed")), "ghp_x")
        self.assertIn(G.AGENT_SA, seen[0].full_url)
        self.assertEqual(seen[0].get_header("Authorization"), "Bearer fed")
        self.assertEqual(seen[1].get_header("Authorization"), "Bearer sa")

    def test_id_token_defaults_to_the_console_iap_client(self):
        seen: list = []
        with mock.patch.object(G.urllib.request, "urlopen", side_effect=_fake({":generateIdToken": {"token": "idt"}}, seen)):
            self.assertEqual(G.sa_id_token("fed"), "idt")
        self.assertEqual(json.loads(seen[0].data)["audience"], G.IAP_CLIENT_ID)

    def test_failure_is_one_line_on_stderr_and_nothing_on_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(G, "cursor_oidc_token", side_effect=OSError("no socket")), \
             mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            self.assertEqual(G.main(["secret", "github-bot-pat"]), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("github-bot-pat unavailable — no socket", err.getvalue())


class ReadTests(unittest.TestCase):
    def test_bq_rows_are_typed_dicts(self):
        seen: list = []
        body = {"jobComplete": True, "jobReference": {"jobId": "j"},
                "schema": {"fields": [{"name": "run_date", "type": "DATE"}, {"name": "exit_code", "type": "INTEGER"},
                                      {"name": "ok", "type": "BOOLEAN"}]},
                "rows": [{"f": [{"v": "2026-11-07"}, {"v": "1"}, {"v": "false"}]}]}
        with mock.patch.object(G.urllib.request, "urlopen", side_effect=_fake({"/queries": body}, seen)):
            rows = G.bq_query("SELECT 1", "sa")
        self.assertEqual(rows, [{"run_date": "2026-11-07", "exit_code": 1, "ok": False}])
        self.assertFalse(json.loads(seen[0].data)["useLegacySql"])

    def test_logs_filter_scopes_to_job_window_and_message(self):
        now = G.datetime.datetime(2026, 11, 8, 5, 0, tzinfo=G.datetime.timezone.utc)
        f = G.logs_filter(job="bhaga-daily-refresh", service=None, since="2d", grep="authfail",
                          extra=None, now=now)
        self.assertIn('timestamp>="2026-11-06T05:00:00Z"', f)
        self.assertIn('resource.labels.job_name="bhaga-daily-refresh"', f)
        self.assertIn('textPayload=~"authfail"', f)

    def test_bad_since_is_rejected(self):
        with self.assertRaises(ValueError):
            G.logs_filter(job=None, service=None, since="yesterday", grep=None, extra=None)

    def test_gcs_get_quotes_the_object_path(self):
        seen: list = []

        def urlopen(req, timeout=0):
            seen.append(req)
            return _Resp(b"png")
        with mock.patch.object(G.urllib.request, "urlopen", side_effect=urlopen), \
             mock.patch("builtins.open", mock.mock_open()) as m:
            self.assertEqual(G.gcs_get("gs://bhaga-scrape-cache/2026-11-07/evidence/a.png", "/tmp/a.png", "sa"), 3)
        self.assertIn("/b/bhaga-scrape-cache/o/2026-11-07%2Fevidence%2Fa.png?alt=media", seen[0].full_url)
        m().write.assert_called_once_with(b"png")


if __name__ == "__main__":
    unittest.main()
