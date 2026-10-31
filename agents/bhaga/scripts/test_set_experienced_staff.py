"""Unit tests for set_experienced_staff — first-name resolution and the rules edit; no BigQuery."""
from __future__ import annotations

import json
import unittest

from agents.bhaga.scripts.set_experienced_staff import first_name, resolve, with_experienced


class ResolveTests(unittest.TestCase):
    ROSTER = ["Doe, Ana", "Roe, Ben Q", "Poe, Ben", "Cole, Cy", "Solo"]

    def test_first_name_of_canonical(self):
        self.assertEqual([first_name(n) for n in self.ROSTER], ["ana", "ben", "ben", "cy", "solo"])

    def test_unique_match_resolves_and_ambiguous_or_missing_refuses(self):
        self.assertEqual(resolve(["ana", "Cy"], self.ROSTER), (["Cole, Cy", "Doe, Ana"], {}))
        self.assertEqual(resolve(["Ana", "Ben", "Zed"], self.ROSTER), (["Doe, Ana"], {"Ben": 2, "Zed": 0}))


class RulesEditTests(unittest.TestCase):
    def test_replaces_experienced_and_keeps_everything_else(self):
        live = {"dayRules": [], "staffRules": [], "staffing": {"minPeople": 2, "training": {
            "openShifts": 3, "closeShifts": 3, "overlapMin": 240, "experienced": ["Old, One"]}}}
        out = with_experienced(json.dumps(live), ["Doe, Ana"])
        self.assertEqual(out["staffing"]["minPeople"], 2)
        self.assertEqual(out["staffing"]["training"],
                         {"openShifts": 3, "closeShifts": 3, "overlapMin": 240, "experienced": ["Doe, Ana"]})

    def test_defaults_when_training_was_never_saved(self):
        out = with_experienced(json.dumps({"dayRules": [], "staffRules": [], "staffing": {"minPeople": 2}}), ["Doe, Ana"])
        self.assertEqual(out["staffing"]["training"],
                         {"openShifts": 2, "closeShifts": 2, "overlapMin": 180, "experienced": ["Doe, Ana"]})

    def test_refuses_without_a_staffing_section(self):
        with self.assertRaises(ValueError):
            with_experienced(json.dumps({"dayRules": [], "staffRules": []}), ["Doe, Ana"])


if __name__ == "__main__":
    unittest.main()
