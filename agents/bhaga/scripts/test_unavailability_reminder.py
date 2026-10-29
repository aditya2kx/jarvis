"""Unavailability reminder (Issues #381, #435): compose, flags, day/time gate, daily dedupe, delivery."""

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
D = dt.date.fromisoformat


class Compose(unittest.TestCase):
    def test_golden_cases(self):
        for case in GOLDEN["cases"]:
            with self.subTest(case["name"]):
                kind, content = ur.compose(
                    D(case["today"]), case["days"],
                    case.get("template", ur.DEFAULT_TEMPLATE), case.get("followup", ur.DEFAULT_FOLLOWUP),
                    lead_weeks=case.get("lead_weeks", ur.DEFAULT_LEAD_WEEKS), flagged=case.get("flagged", []),
                )
                self.assertEqual((kind, content), (case["kind"], case["content"]))

    def test_edited_template_keeps_placeholders(self):
        _kind, content = ur.compose(
            D("2026-10-28"), [2, 3], "Week {target_week}, through {through_date} by {publish_day} ({lead_weeks}w)!", ""
        )
        self.assertTrue(content.endswith("Week Nov 23 - Nov 29, through Nov 29 by Friday(Oct 30) (4w)!"))

    def test_lead_weeks_is_clamped(self):
        self.assertEqual(ur.lead_weeks_of({}), 4)
        self.assertEqual(ur.lead_weeks_of({"lead_weeks": 1}), 2)
        self.assertEqual(ur.lead_weeks_of({"lead_weeks": 20}), 8)


class Flags(unittest.TestCase):
    def test_golden_unmarked(self):
        for case in GOLDEN["unmarked"]:
            with self.subTest(case["name"]):
                weeks = ur.check_weeks(D(case["today"]), case["lead_weeks"])
                self.assertEqual([w.isoformat() for w in weeks], case["check_weeks"])
                rows = [{**r, "first_date": D(r["first_date"]),
                         "repeat_until": D(r["repeat_until"]) if r["repeat_until"] else None}
                        for r in case["rows"]]
                self.assertEqual(ur.unmarked(case["roster"], rows, weeks), case["flagged"])

    def test_member_match(self):
        members = [
            {"id": "1", "username": "Dana Lee"},
            {"id": "2", "username": "Sam Roe"},
            {"id": "3", "username": "Sam Park"},
            {"id": "4", "username": "Bo"},
        ]
        self.assertEqual(ur.mention("Lee, Dana M", members), "[@Dana Lee](#user_mention#1)")
        self.assertEqual(ur.mention("Roe, Sam", members), "[@Sam Roe](#user_mention#2)")
        self.assertEqual(ur.mention("Ng, Bo", members), "[@Bo](#user_mention#4)")
        # Two Sams and no last-name match: never guess.
        self.assertEqual(ur.mention("Kim, Sam", members), "@Sam Kim")
        self.assertEqual(ur.mention("Nobody, Here", members), "@Here Nobody")


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
           "workspace_id": "9017956545", "template": ur.DEFAULT_TEMPLATE, "followup_template": None,
           "lead_weeks": 4}

    def _run(self, now, *, posted_today=False, cfg=None, browser=None, flagged=(), **kw):
        sent, recorded, typed = [], [], []

        def fake_browser(store, channel_id, content, workspace):
            if browser:
                raise browser
            typed.append((channel_id, content))
            return {"everyone": True, "users": [], "plain": []}

        with mock.patch.object(ur, "ensure_default_config", return_value={**self.CFG, **(cfg or {})}), \
             mock.patch.object(ur.tp, "already_posted", return_value=posted_today), \
             mock.patch.object(ur.tp, "resolve_target_channel", return_value=("dm", "dm1")), \
             mock.patch.object(ur.tp, "record_post", lambda **k: recorded.append(k)), \
             mock.patch.object(ur, "flagged_mentions", **(
                 {"side_effect": flagged} if isinstance(flagged, Exception) else {"return_value": list(flagged)})), \
             mock.patch.object(ur, "browser_send", fake_browser), \
             mock.patch("skills.clickup_chat.post_message",
                        lambda ch, content, team_id: sent.append(content) or {"id": "m1"}):
            result = ur.run_reminder(now=now, **kw)
        return result, sent, recorded, typed

    def test_wednesday_tick_sends_one_reminder_and_logs_it(self):
        result, sent, recorded, _ = self._run(dt.datetime(2026, 10, 28, 10, 0, tzinfo=CT))
        self.assertEqual((result["status"], result["kind"]), ("posted", "reminder"))
        self.assertEqual(sent, [GOLDEN["cases"][0]["content"]])
        self.assertEqual(recorded[0]["automation_id"], "unavailability-reminder")

    def test_thursday_falls_back_to_the_default_followup_and_tags(self):
        result, sent, _, _ = self._run(dt.datetime(2026, 10, 29, 10, 0, tzinfo=CT),
                                       flagged=GOLDEN["cases"][1]["flagged"])
        self.assertEqual(sent, [GOLDEN["cases"][1]["content"]])

    def test_second_tick_same_day_sends_nothing(self):
        result, sent, recorded, _ = self._run(dt.datetime(2026, 10, 28, 11, 0, tzinfo=CT), posted_today=True)
        self.assertEqual((result["status"], sent, recorded), ("skipped", [], []))

    def test_off_day_and_before_time_send_nothing(self):
        for now in (dt.datetime(2026, 10, 30, 10, tzinfo=CT), dt.datetime(2026, 10, 28, 9, 45, tzinfo=CT)):
            result, sent, _, _ = self._run(now)
            self.assertEqual((result["status"], sent), ("skipped", []))

    def test_once_ignores_the_gate_but_not_the_dedupe(self):
        result, sent, _, _ = self._run(dt.datetime(2026, 10, 30, 8, tzinfo=CT), force=True)
        self.assertEqual(result["status"], "posted")
        result, sent, _, _ = self._run(dt.datetime(2026, 10, 30, 8, tzinfo=CT), force=True, posted_today=True)
        self.assertEqual((result["status"], sent), ("skipped", []))

    def test_dry_run_posts_nothing(self):
        result, sent, recorded, _ = self._run(dt.datetime(2026, 10, 28, 10, tzinfo=CT), dry_run=True)
        self.assertEqual((result["status"], sent, recorded), ("dry_run", [], []))

    def test_drafts_tick_outside_the_job_only_starts_the_job(self):
        started = []
        result, sent, recorded, typed = self._run(
            dt.datetime(2026, 10, 28, 10, tzinfo=CT), cfg={"destination": "drafts"},
            start_browser_job=lambda: started.append(1) or True,
        )
        self.assertEqual((result["status"], started, sent, recorded, typed), ("queued", [1], [], [], []))

    def test_drafts_in_the_job_types_into_the_drafts_channel(self):
        result, sent, recorded, typed = self._run(
            dt.datetime(2026, 10, 28, 10, tzinfo=CT), cfg={"destination": "drafts", "channel_id": "ch9"},
            in_browser_job=True,
        )
        self.assertEqual((result["status"], result["destination"], sent), ("posted", "drafts", []))
        self.assertEqual(typed, [("ch9", GOLDEN["cases"][0]["content"])])
        self.assertEqual((recorded[0]["destination"], recorded[0]["channel_id"]), ("drafts", "ch9"))

    def test_browser_failure_falls_back_to_the_rest_dm(self):
        result, sent, recorded, typed = self._run(
            dt.datetime(2026, 10, 28, 10, tzinfo=CT), cfg={"destination": "drafts"},
            in_browser_job=True, browser=RuntimeError("no ClickUp session"),
        )
        self.assertEqual((result["status"], result["destination"], typed), ("posted", "dm", []))
        self.assertTrue(sent[0].startswith(GOLDEN["cases"][0]["content"]))
        self.assertIn("browser send failed: no ClickUp session", sent[0])
        self.assertEqual(recorded[0]["channel_id"], "dm1")

    def test_flag_lookup_failure_still_sends_untagged(self):
        result, sent, _, _ = self._run(dt.datetime(2026, 10, 29, 10, tzinfo=CT), flagged=RuntimeError("bq down"))
        self.assertEqual((result["status"], sent), ("posted", [GOLDEN["cases"][2]["content"]]))


if __name__ == "__main__":
    unittest.main()
