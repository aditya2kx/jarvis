"""delete_dashboard treats HTTP 404 as already-gone (Issue #276)."""

from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from skills.grafana_cloud_provisioning import register


class TestDeleteDashboard(unittest.TestCase):
    def test_404_is_already_gone(self):
        err = HTTPError(
            "https://example.grafana.net/api/dashboards/uid/bhaga-analytics-v1",
            404,
            "Not Found",
            hdrs=None,
            fp=BytesIO(b'{"message":"not found"}'),
        )
        with patch.object(register, "_get_token", return_value="t"), \
             patch.object(register.urllib.request, "urlopen", side_effect=err):
            self.assertEqual(
                register.delete_dashboard("bhaga-analytics-v1", org_slug="steadyangelfish2985"),
                "already-gone",
            )

    def test_200_returns_message(self):
        class _Resp:
            def read(self):
                return json.dumps({"message": "Dashboard deleted"}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch.object(register, "_get_token", return_value="t"), \
             patch.object(register.urllib.request, "urlopen", return_value=_Resp()):
            self.assertEqual(
                register.delete_dashboard("bhaga-analytics-v1", org_slug="x"),
                "Dashboard deleted",
            )
