"""Unit tests for set_experienced_staff — first-name resolution and the rules edit; no BigQuery."""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from agents.bhaga.scripts import set_experienced_staff as S
from agents.bhaga.scripts.set_experienced_staff import first_name, resolve, with_experienced


class FakeClient:
    """Answers the three queries main() issues: roster, live version, versioned INSERT."""

    def __init__(self, roster, live, inserted=1):
        self.roster, self.live, self.inserted, self.insert_params = roster, live, inserted, None

    def query(self, sql, job_config=None):
        params = {p.name: p.value for p in job_config.query_parameters}
        if "adp_directory_status" in sql:
            rows = [SimpleNamespace(employee=n) for n in self.roster]
        elif sql.lstrip().startswith("INSERT"):
            self.insert_params = params
            return SimpleNamespace(result=lambda: [], num_dml_affected_rows=self.inserted)
        else:
            rows = [SimpleNamespace(version=v, rules_json=r) for v, r in self.live]
        return SimpleNamespace(result=lambda: rows)


class MainTests(unittest.TestCase):
    LIVE = [(7, json.dumps({"dayRules": [], "staffRules": [], "staffing": {"minPeople": 2}}))]

    def _main(self, client, *names):
        out = io.StringIO()
        with mock.patch.object(S, "get_client", return_value=client), contextlib.redirect_stdout(out):
            rc = S.main(["--by", "prod-ops PR #457", *names])
        return rc, out.getvalue()

    def test_saves_the_next_version_with_resolved_names_and_prints_no_names(self):
        c = FakeClient(["Doe, Ana", "Cole, Cy"], self.LIVE)
        rc, out = self._main(c, "Ana", "Cy")
        self.assertEqual(rc, 0)
        self.assertEqual(c.insert_params["next"], 8)
        self.assertEqual(c.insert_params["by"], "prod-ops PR #457")
        rules = json.loads(c.insert_params["rules"])
        self.assertEqual(rules["staffing"]["training"]["experienced"], ["Cole, Cy", "Doe, Ana"])
        self.assertEqual(rules["staffing"]["minPeople"], 2)
        self.assertIn("version 7 → 8 experienced=2", out)
        self.assertNotIn("Doe", out)

    def test_refuses_without_writing(self):
        for client, names, why in (
            (FakeClient(["Doe, Ana"], self.LIVE), ["Zed"], "exactly one Active match"),
            (FakeClient(["Doe, Ana"], []), ["Ana"], "no saved rules"),
            (FakeClient(["Doe, Ana"], [(1, json.dumps({"dayRules": [], "staffRules": []}))]), ["Ana"], "no staffing"),
        ):
            rc, out = self._main(client, *names)
            self.assertEqual(rc, 2, why)
            self.assertIn(why, out)
            self.assertIsNone(client.insert_params, why)

    def test_a_save_that_lands_first_is_reported_not_overwritten(self):
        rc, out = self._main(FakeClient(["Doe, Ana"], self.LIVE, inserted=0), "Ana")
        self.assertEqual(rc, 2)
        self.assertIn("rules changed past version 7", out)


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
