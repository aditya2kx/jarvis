"""Unit tests for set_shift_windows — parsing, the rules edit and the versioned save; no BigQuery."""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest import mock

from agents.bhaga.scripts import set_shift_windows as S
from agents.bhaga.scripts.set_shift_windows import parse_days, parse_window, with_shift_windows
from agents.bhaga.scripts.test_set_experienced_staff import FakeClient


class ParseTests(unittest.TestCase):
    def test_window(self):
        self.assertEqual(parse_window("09:00-17:00"), {"fromMin": 540, "toMin": 1020})
        self.assertEqual(parse_window("16:00-20:30"), {"fromMin": 960, "toMin": 1230})
        for bad in ("9-5", "17:00-09:00", "09:00-09:00", "09:75-10:00", "10:00-25:00"):
            with self.assertRaises(ValueError, msg=bad):
                parse_window(bad)

    def test_days_mon_is_zero_and_default_is_every_day(self):
        self.assertEqual(parse_days(None), list(range(7)))
        self.assertEqual(parse_days("Wed,mon,Sun"), [0, 2, 6])
        with self.assertRaises(ValueError):
            parse_days("Xyz")


class RulesEditTests(unittest.TestCase):
    LIVE = {"dayRules": [], "staffing": {"minPeople": 2}, "staffRules": [
        {"id": "a", "employee": "Kim, Dee", "kind": "shift_windows", "value": 0, "days": [0],
         "windows": [{"fromMin": 600, "toMin": 700}]},
        {"id": "b", "employee": "Kim, Dee", "kind": "max_week_hours", "value": 20},
        {"id": "c", "employee": "Lee, Ana", "kind": "shift_windows", "value": 0, "days": [1],
         "windows": [{"fromMin": 600, "toMin": 700}]},
    ]}

    def test_replaces_only_this_persons_windows(self):
        out = with_shift_windows(json.dumps(self.LIVE), "Kim, Dee",
                                 [parse_window("16:00-20:30"), parse_window("09:00-17:00")], [0, 1])
        self.assertEqual(out["staffing"], {"minPeople": 2})
        kept = [(r["employee"], r["kind"]) for r in out["staffRules"]]
        self.assertEqual(kept, [("Kim, Dee", "max_week_hours"), ("Lee, Ana", "shift_windows"), ("Kim, Dee", "shift_windows")])
        new = out["staffRules"][-1]
        self.assertEqual(new["days"], [0, 1])
        self.assertEqual(new["windows"], [{"fromMin": 540, "toMin": 1020}, {"fromMin": 960, "toMin": 1230}])
        self.assertNotIn(new["id"], {"a", "b", "c"})


class MainTests(unittest.TestCase):
    LIVE = [(7, json.dumps({"dayRules": [], "staffRules": [], "staffing": {"minPeople": 2}}))]

    def _main(self, client, *args):
        out = io.StringIO()
        with mock.patch.object(S, "get_client", return_value=client), contextlib.redirect_stdout(out):
            rc = S.main(["--by", "prod-ops PR #480", *args])
        return rc, out.getvalue()

    def test_saves_the_next_version_and_prints_no_names(self):
        c = FakeClient(["Kim, Dee", "Lee, Ana"], self.LIVE)
        rc, out = self._main(c, "Dee", "09:00-17:00", "16:00-20:30")
        self.assertEqual(rc, 0)
        self.assertEqual(c.insert_params["next"], 8)
        rule = json.loads(c.insert_params["rules"])["staffRules"][0]
        self.assertEqual((rule["employee"], rule["kind"], rule["days"]), ("Kim, Dee", "shift_windows", list(range(7))))
        self.assertIn("version 7 → 8 windows=2 days=7", out)
        self.assertNotIn("Kim", out)

    def test_refuses_without_writing(self):
        for client, args, why in (
            (FakeClient(["Kim, Dee"], self.LIVE), ["Zed", "09:00-17:00"], "exactly one Active match"),
            (FakeClient(["Kim, Dee"], []), ["Dee", "09:00-17:00"], "no saved rules"),
            (FakeClient(["Kim, Dee"], self.LIVE), ["Dee", "17:00-09:00"], "from time before"),
            (FakeClient(["Kim, Dee"], self.LIVE), ["Dee", *["09:00-10:00"] * 5], "at most 4"),
        ):
            rc, out = self._main(client, *args)
            self.assertEqual(rc, 2, why)
            self.assertIn(why, out)
            self.assertIsNone(client.insert_params, why)

    def test_a_save_that_lands_first_is_reported_not_overwritten(self):
        rc, out = self._main(FakeClient(["Kim, Dee"], self.LIVE, inserted=0), "Dee", "09:00-17:00")
        self.assertEqual(rc, 2)
        self.assertIn("rules changed past version 7", out)


if __name__ == "__main__":
    unittest.main()
