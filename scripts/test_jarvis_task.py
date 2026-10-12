import unittest
import jarvis_task as T


class ParseTests(unittest.TestCase):
    def test_routing_word(self):
        self.assertEqual(T.parse("mac fix the auth bug"), ("mac", "fix the auth bug"))
        self.assertEqual(T.parse("Cloud: add a chip"), ("cloud", "add a chip"))
        self.assertEqual(T.parse("where=mac x"), ("mac", "x"))

    def test_no_word_and_lookalikes_are_not_routing(self):
        self.assertEqual(T.parse("add a chip"), (None, "add a chip"))
        self.assertEqual(T.parse("macaroni menu"), (None, "macaroni menu"))
        self.assertEqual(T.parse("cloudflare setup"), (None, "cloudflare setup"))

    def test_default_follows_the_surface(self):
        self.assertEqual(T.default_where("mac_local"), "mac")
        self.assertEqual(T.default_where("cursor_cloud"), "cloud")


class CloudTests(unittest.TestCase):
    def test_new_issue_when_none_referenced(self):
        (c,) = T.cloud_commands("add a chip")
        self.assertEqual(c[:3], ["gh", "issue", "create"])
        self.assertIn("jarvis-work", c)

    def test_existing_issue_is_commented_not_duplicated(self):
        (c,) = T.cloud_commands("fix the auth bug #42")
        self.assertEqual(c[:4], ["gh", "issue", "comment", "42"])
        self.assertEqual(c[-1], "/jarvis-new-task fix the auth bug #42")


if __name__ == "__main__":
    unittest.main()
