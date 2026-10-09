"""Unavailability reminder (Issue #381): compose, day/time gate, daily dedupe."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

from agents.bhaga.scripts import unavailability_reminder as ur

GOLDEN = json.loads(
    (pathlib.Path(__file__).resolve().parents[3] / "core/testdata/unavailability_reminder_golden.json").read_text()
)
CT = ZoneInfo("America/Chicago")


class Compose(unittest.TestCase):
    def test_golden_cases(self):
        for case in GOLDEN["cases"]:
            with self.subTest(case["name"]):
                kind, content = ur.compose(
                    dt.date.fromisoformat(case["today"]), case["days"], ur.DEFAULT_TEMPLATE, ur.DEFAULT_FOLLOWUP
                )
                self.assertEqual((kind, content), (case["kind"], case["content"]))

    def test_edited_template_keeps_placeholders(self):
        _kind, content = ur.compose(dt.date(2026, 10, 7), [2, 3], "Week {target_week} by {publish_day}!", "")
        self.assertTrue(content.endswith("Week Oct 19 - Oct 25 by Friday(Oct 9)!"))


class Gate(unittest.TestCase):
    def at(self, day, hour, minute=0):
        return dt.datetime(2026, 10, day, hour, minute, tzinfo=CT)

    def test_due_from_the_configured_time_on_configured_days(self):
        due = lambda now: ur.is_due(now, days=[2, 3], hour=10, minute=0, enabled=True)  # noqa: E731
        self.assertFalse(due(self.at(7, 9, 45)))
        self.assertTrue(due(self.at(7, 10)))
        self.assertTrue(due(self.at(8, 15)))
        self.assertFalse(due(self.at(9, 10)))

    def test_disabled_is_never_due(self):
        self.assertFalse(ur.is_due(self.at(7, 11), days=[2, 3], hour=10, minute=0, enabled=False))


class Run(unittest.TestCase):
    CFG = {"enabled": True, "days_of_week": "[2,3]", "hour_local": 10, "minute_local": 0,
           "timezone": "America/Chicago", "destination": "dm", "dm_user_id": "198109189",
           "workspace_id": "9017956545", "template": ur.DEFAULT_TEMPLATE, "followup_template": None}

    def _run(self, now, *, posted_today=False, **kw):
        sent, recorded = [], []
        with mock.patch.object(ur, "ensure_default_config", return_value=dict(self.CFG)), \
             mock.patch.object(ur.tp, "already_posted", return_value=posted_today), \
             mock.patch.object(ur.tp, "resolve_target_channel", return_value=("dm", "dm1")), \
             mock.patch.object(ur.tp, "record_post", lambda **k: recorded.append(k)), \
             mock.patch("skills.clickup_chat.post_message",
                        lambda ch, content, team_id: sent.append(content) or {"id": "m1"}):
            result = ur.run_reminder(now=now, **kw)
        return result, sent, recorded

    def test_wednesday_tick_sends_one_reminder_and_logs_it(self):
        result, sent, recorded = self._run(dt.datetime(2026, 10, 7, 10, 0, tzinfo=CT))
        self.assertEqual((result["status"], result["kind"]), ("posted", "reminder"))
        self.assertEqual(sent, [GOLDEN["cases"][0]["content"]])
        self.assertEqual(recorded[0]["automation_id"], "unavailability-reminder")

    def test_thursday_falls_back_to_the_default_followup(self):
        result, sent, _ = self._run(dt.datetime(2026, 10, 8, 10, 0, tzinfo=CT))
        self.assertEqual(sent, [GOLDEN["cases"][1]["content"]])

    def test_second_tick_same_day_sends_nothing(self):
        result, sent, recorded = self._run(dt.datetime(2026, 10, 7, 11, 0, tzinfo=CT), posted_today=True)
        self.assertEqual((result["status"], sent, recorded), ("skipped", [], []))

    def test_off_day_and_before_time_send_nothing(self):
        for now in (dt.datetime(2026, 10, 9, 10, tzinfo=CT), dt.datetime(2026, 10, 7, 9, 45, tzinfo=CT)):
            result, sent, _ = self._run(now)
            self.assertEqual((result["status"], sent), ("skipped", []))

    def test_once_ignores_the_gate_but_not_the_dedupe(self):
        result, sent, _ = self._run(dt.datetime(2026, 10, 9, 8, tzinfo=CT), force=True)
        self.assertEqual(result["status"], "posted")
        result, sent, _ = self._run(dt.datetime(2026, 10, 9, 8, tzinfo=CT), force=True, posted_today=True)
        self.assertEqual((result["status"], sent), ("skipped", []))

    def test_dry_run_posts_nothing(self):
        result, sent, recorded = self._run(dt.datetime(2026, 10, 7, 10, tzinfo=CT), dry_run=True)
        self.assertEqual((result["status"], sent, recorded), ("dry_run", [], []))


if __name__ == "__main__":
    unittest.main()
