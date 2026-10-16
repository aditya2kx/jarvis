"""Thread replies, reactions and members (Issue #397 missing-punch check)."""

from __future__ import annotations

import unittest
from unittest import mock

from skills.clickup_chat import runner


class ThreadHelpers(unittest.TestCase):
    def test_fetch_replies_pages_and_sorts_oldest_first(self):
        pages = [
            {"data": [{"id": "r3", "date": 3}, {"id": "r2", "date": 2}], "next_cursor": "c1"},
            {"data": [{"id": "r1", "date": 1}]},
        ]
        with mock.patch.object(runner, "_request", side_effect=pages) as req, \
                mock.patch.object(runner.time, "sleep"):
            got = runner.fetch_replies("m1", team_id="9", pat="pk_x")
        self.assertEqual([r["id"] for r in got], ["r1", "r2", "r3"])
        self.assertIn("/chat/messages/m1/replies?limit=50&cursor=c1", req.call_args_list[1].args[0])

    def test_add_reaction_posts_lowercase_name_and_tolerates_duplicates(self):
        with mock.patch.object(runner, "_request", return_value={}) as req:
            self.assertTrue(runner.add_reaction("r1", team_id="9", pat="pk_x"))
        self.assertEqual(req.call_args.kwargs["payload"], {"reaction": "white_check_mark"})
        dup = RuntimeError("ClickUp API error 400 on POST ...: reaction already exists")
        with mock.patch.object(runner, "_request", side_effect=dup):
            self.assertTrue(runner.add_reaction("r1", team_id="9", pat="pk_x"))
        with mock.patch.object(runner, "_request", side_effect=RuntimeError("ClickUp API error 404")):
            with self.assertRaises(RuntimeError):
                runner.add_reaction("r1", team_id="9", pat="pk_x")

    def test_list_members_reads_the_workspace_with_lowercase_emails(self):
        team = {"teams": [
            {"id": "1", "members": [{"user": {"id": 5, "username": "x", "email": "x@y"}}]},
            {"id": "9", "members": [{"user": {"id": 7, "username": "Sam O", "email": " Sam@X.io "}},
                                    {"user": {}}]},
        ]}
        with mock.patch.object(runner, "_request", return_value=team):
            self.assertEqual(runner.list_members("9", pat="pk_x"),
                             [{"id": "7", "username": "Sam O", "email": "sam@x.io"}])

    def test_mention_is_the_inline_chip_syntax(self):
        self.assertEqual(runner.mention("7", "Sam"), "[@Sam](#user_mention#7)")


if __name__ == "__main__":
    unittest.main()
