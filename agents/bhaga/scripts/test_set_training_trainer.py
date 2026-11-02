"""Unit tests for set_training_trainer — rules edit and main(); no BigQuery."""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from agents.bhaga.scripts import set_training_trainer as S
from agents.bhaga.scripts.set_training_trainer import with_trainer
from agents.bhaga.scripts.test_set_experienced_staff import FakeClient


class RulesEditTests(unittest.TestCase):
    def test_adds_experienced_unmarks_and_sets_closers_only(self):
        live = {"dayRules": [], "staffRules": [], "staffing": {"minPeople": 2, "training": {
            "openShifts": 2, "closeShifts": 2, "overlapMin": 180,
            "experienced": ["Doe, Ana"], "trained": ["Zee, Wing"],
            "trainsOnly": {"Doe, Ana": "open"}, "trainedOnly": {"Zee, Wing": "close"}}}}
        out = with_trainer(json.dumps(live), "Zee, Wing", "close")
        t = out["staffing"]["training"]
        self.assertEqual(t["experienced"], ["Doe, Ana", "Zee, Wing"])
        self.assertEqual(t["trained"], [])
        self.assertEqual(t["trainsOnly"], {"Doe, Ana": "open", "Zee, Wing": "close"})
        self.assertEqual(t["trainedOnly"], {})

    def test_both_clears_trains_only_and_keeps_others(self):
        live = {"staffing": {"training": {
            "experienced": ["Zee, Wing"], "trained": [],
            "trainsOnly": {"Zee, Wing": "close", "Doe, Ana": "open"}, "trainedOnly": {}}}}
        out = with_trainer(json.dumps(live), "Zee, Wing", None)
        self.assertEqual(out["staffing"]["training"]["trainsOnly"], {"Doe, Ana": "open"})
        self.assertEqual(out["staffing"]["training"]["experienced"], ["Zee, Wing"])

    def test_defaults_when_training_was_never_saved(self):
        out = with_trainer(json.dumps({"staffing": {"minPeople": 2}}), "Zee, Wing", "open")
        self.assertEqual(out["staffing"]["training"]["experienced"], ["Zee, Wing"])
        self.assertEqual(out["staffing"]["training"]["trainsOnly"], {"Zee, Wing": "open"})
        self.assertEqual(out["staffing"]["training"]["openShifts"], 2)

    def test_refuses_without_a_staffing_section(self):
        with self.assertRaises(ValueError):
            with_trainer(json.dumps({"dayRules": []}), "Zee, Wing", "close")


class MainTests(unittest.TestCase):
    LIVE = [(27, json.dumps({"staffing": {"training": {
        "experienced": ["Doe, Ana"], "trained": ["Zee, Wing"]}}}))]

    def _main(self, client, *args):
        out = io.StringIO()
        with mock.patch.object(S, "get_client", return_value=client), contextlib.redirect_stdout(out):
            rc = S.main(["--by", "prod-ops PR #484", *args])
        return rc, out.getvalue()

    def test_saves_closers_only_and_prints_no_names(self):
        c = FakeClient(["Doe, Ana", "Zee, Wing"], self.LIVE)
        rc, out = self._main(c, "Wing", "closers-only")
        self.assertEqual(rc, 0)
        self.assertEqual(c.insert_params["next"], 28)
        rules = json.loads(c.insert_params["rules"])
        t = rules["staffing"]["training"]
        self.assertEqual(t["experienced"], ["Doe, Ana", "Zee, Wing"])
        self.assertEqual(t["trained"], [])
        self.assertEqual(t["trainsOnly"], {"Zee, Wing": "close"})
        self.assertIn("version 27 → 28 kind=closers-only", out)
        self.assertNotIn("Zee, Wing", out)

    def test_refuses_without_writing(self):
        for client, args, why in (
            (FakeClient(["Doe, Ana"], self.LIVE), ["Zed", "closers-only"], "exactly one Active match"),
            (FakeClient(["Zee, Wing"], []), ["Wing", "closers-only"], "no saved rules"),
            (FakeClient(["Zee, Wing"], [(1, json.dumps({"dayRules": []}))]), ["Wing", "both"], "no staffing"),
        ):
            rc, out = self._main(client, *args)
            self.assertEqual(rc, 2, why)
            self.assertIn(why, out)
            self.assertIsNone(client.insert_params, why)


if __name__ == "__main__":
    unittest.main()
