"""ClickUp browser sender (Issue #435): mention tokens → typed segments, with a fake page."""

from __future__ import annotations

import unittest

from skills.clickup_browser import sender


class Segments(unittest.TestCase):
    def test_everyone_people_and_text(self):
        self.assertEqual(
            sender.segments("@everyone hi [@Dana Lee](#user_mention#11) and @Sam\nbye"),
            [("everyone", "", ""), ("text", " hi ", ""), ("user", "Dana Lee", "11"),
             ("text", " and @Sam\nbye", "")],
        )

    def test_plain_text_only(self):
        self.assertEqual(sender.segments("no mentions"), [("text", "no mentions", "")])


class _Locator:
    def __init__(self, page, sel, text=None):
        self.page, self.sel, self.text = page, sel, text

    def filter(self, has_text):
        return _Locator(self.page, self.sel, has_text)

    @property
    def first(self):
        return self

    def wait_for(self, **_kw):
        if (self.sel, self.text) not in self.page.options:
            raise TimeoutError(self.sel)

    def click(self):
        self.page.log.append(("pick", self.sel, self.text))

    def count(self):
        return 0


class _Keyboard:
    def __init__(self, log):
        self.log = log

    def type(self, s, delay=0):
        self.log.append(("type", s))

    def insert_text(self, s):
        self.log.append(("insert", s))

    def press(self, k):
        self.log.append(("press", k))


class _Page:
    def __init__(self, options):
        self.options, self.log = options, []
        self.keyboard = _Keyboard(self.log)

    def locator(self, sel):
        return _Locator(self, sel)

    def get_by_role(self, *_a, **_kw):
        return _Locator(self, "dont-add")

    def wait_for_timeout(self, _ms):
        pass


class TypeMessage(unittest.TestCase):
    def test_picks_chips_and_falls_back_to_plain_names(self):
        page = _Page({(sender.OPTION, "Everyone"), (f"{sender.OPTION}#item_11", None)})
        res = sender.type_message(
            page, "Head (#1)\n\n---\n\n@everyone x [@Dana Lee](#user_mention#11) [@Bo Ng](#user_mention#99)\ny",
        )
        self.assertEqual((res.everyone, res.users, res.plain), (True, ["11"], ["Bo Ng"]))
        picks = [e for e in page.log if e[0] == "pick"]
        self.assertEqual(picks, [("pick", sender.OPTION, "Everyone"), ("pick", f"{sender.OPTION}#item_11", None)])
        self.assertEqual([e[1] for e in page.log if e[0] == "type"], ["@every", "@Dana Lee", "@Bo Ng"])
        self.assertIn(("insert", "Head (#1)"), page.log)
        self.assertIn(("insert", "Bo Ng"), page.log)
        self.assertIn(("insert", "x "), page.log)  # the space after the Everyone chip is ClickUp's
        self.assertNotIn(("insert", "---"), page.log)
        self.assertEqual(page.log.count(("press", "Shift+Enter")), 3)  # one blank line for the ---, one \n
        self.assertNotIn(("press", "Enter"), page.log)


if __name__ == "__main__":
    unittest.main()
