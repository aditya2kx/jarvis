"""Unit tests for skills/vault — `op` and xdotool are mocked."""
from __future__ import annotations

import io
import json
import subprocess
import unittest
from unittest import mock

from skills.vault import vault as V

ITEMS = [
    {"id": "g1", "title": "Google (owner)", "urls": [{"href": "https://accounts.google.com/"}]},
    {"id": "a1", "title": "ADP RUN", "urls": [{"href": "https://runpayroll.adp.com"}]},
    {"id": "s1", "title": "Square", "urls": [{"href": "squareup.com"}, {"href": "https://www.squareup.com/login"}]},
]


def _run(outputs: dict):
    def run(args, **kw):
        if args[0] == "op":
            key = args[1:3] if args[1:3] != ["item", "get"] else ("get", args[4] if len(args) > 4 else "")
            out = outputs[tuple(key)]
            return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")
        return subprocess.CompletedProcess(args, 0)
    return run


class ResolveTests(unittest.TestCase):
    def setUp(self):
        with mock.patch.dict("os.environ", {"OP_SERVICE_ACCOUNT_TOKEN": "t"}), \
             mock.patch.object(V.subprocess, "run", side_effect=_run({("item", "list"): json.dumps(ITEMS)})):
            self.items = V.list_items()

    def test_lists_sites_by_host(self):
        self.assertEqual(self.items[2]["sites"], ["squareup.com"])

    def test_resolves_title_host_and_parent_domain(self):
        self.assertEqual(V.resolve("ADP RUN", self.items), "a1")
        self.assertEqual(V.resolve("accounts.google.com", self.items), "g1")
        self.assertEqual(V.resolve("https://squareup.com/dashboard", self.items), "s1")
        self.assertEqual(V.resolve("app.squareup.com", self.items), "s1")

    def test_unknown_site_is_a_clear_error(self):
        with self.assertRaisesRegex(V.VaultError, "no Login item"):
            V.resolve("example.com", self.items)


class SecretHandlingTests(unittest.TestCase):
    def _main(self, argv, outputs):
        out = io.StringIO()
        calls = []

        def run(args, **kw):
            calls.append((args, kw))
            return _run(outputs)(args, **kw)

        with mock.patch.dict("os.environ", {"OP_SERVICE_ACCOUNT_TOKEN": "t"}), \
             mock.patch.object(V.subprocess, "run", side_effect=run), mock.patch("sys.stdout", out):
            rc = V.main(argv)
        return rc, out.getvalue(), calls

    def test_type_sends_password_to_xdotool_stdin_never_stdout(self):
        rc, out, calls = self._main(["type", "ADP RUN", "--field", "password", "--enter"],
                                    {("item", "list"): json.dumps(ITEMS), ("get", "--fields"): "hunter2\n"})
        self.assertEqual(rc, 0)
        self.assertNotIn("hunter2", out)
        xdo = [kw for args, kw in calls if args[0] == "xdotool" and args[1] == "type"]
        self.assertEqual(xdo[0]["input"], "hunter2")
        self.assertIn(["xdotool", "key", "Return"], [args for args, _ in calls])

    def test_get_prints_username_only(self):
        rc, out, calls = self._main(["get", "ADP RUN"],
                                    {("item", "list"): json.dumps(ITEMS), ("get", "--fields"): "ops@example.com\n"})
        self.assertEqual((rc, out), (0, "username=ops@example.com\n"))
        self.assertIn("label=username", calls[1][0])

    def test_otp_uses_op_otp(self):
        rc, _, calls = self._main(["type", "Square", "--field", "otp"],
                                  {("item", "list"): json.dumps(ITEMS), ("get", "--otp"): "123456\n"})
        self.assertEqual(rc, 0)
        self.assertIn("--otp", calls[1][0])

    def test_run_takes_options_after_site_and_passes_secret_in_child_env(self):
        rc, out, calls = self._main(["run", "ADP RUN", "--field", "password", "--as", "ADP_PASSWORD",
                                     "--", "python3", "login.py", "--fast"],
                                    {("item", "list"): json.dumps(ITEMS), ("get", "--fields"): "hunter2\n"})
        self.assertEqual(rc, 0)
        self.assertNotIn("hunter2", out)
        args, kw = calls[-1]
        self.assertEqual(args, ["python3", "login.py", "--fast"])
        self.assertEqual(kw["env"]["ADP_PASSWORD"], "hunter2")

    def test_missing_token_fails_without_calling_op(self):
        err = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(V.subprocess, "run") as run, \
             mock.patch("sys.stderr", err):
            self.assertEqual(V.main(["list"]), 1)
        run.assert_not_called()
        self.assertIn("OP_SERVICE_ACCOUNT_TOKEN", err.getvalue())


if __name__ == "__main__":
    unittest.main()
