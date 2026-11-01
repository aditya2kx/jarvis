"""Unit tests for scripts/delete_actions_run.py — no live GitHub calls."""
from __future__ import annotations

import unittest
from unittest import mock

from scripts import delete_actions_run as dar


class ParseRunId(unittest.TestCase):
    def test_numeric(self):
        self.assertEqual(dar.run_id("41270855462"), 41270855462)

    def test_url(self):
        self.assertEqual(
            dar.run_id("https://github.com/aditya2kx/jarvis/actions/runs/41270855462"),
            41270855462,
        )


class DeleteRun(unittest.TestCase):
    def test_204(self):
        with mock.patch("urllib.request.urlopen") as u:
            u.return_value.__enter__.return_value.status = 204
            self.assertEqual(dar.delete_run(1, token="t"), 204)
        req = u.call_args.args[0]
        self.assertEqual(req.get_method(), "DELETE")
        self.assertIn("/actions/runs/1", req.full_url)

    def test_already_gone_is_ok(self):
        import urllib.error

        err = urllib.error.HTTPError("u", 404, "gone", hdrs=None, fp=mock.Mock(read=lambda: b""))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertEqual(dar.delete_run(1, token="t"), 404)


if __name__ == "__main__":
    unittest.main()
