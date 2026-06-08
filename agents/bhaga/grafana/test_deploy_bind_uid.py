"""bind_datasource_uid must replace every `${ds_bigquery}` reference with the
real datasource UID before the dashboard is pushed.

Regression guard for the "every panel shows No data" bug: the `ds_bigquery`
template variable stored the datasource *name* ("BHAGA BigQuery"), but panels
reference `"uid": "${ds_bigquery}"`, so Grafana could not resolve the datasource
("Data source not found"). The deploy step now binds the literal UID.
"""

import json
import pathlib
import unittest

from agents.bhaga.grafana.deploy import bind_datasource_uid

_DASHBOARD_JSON = pathlib.Path(__file__).parent / "dashboard.json"
_REAL_UID = "efo24wc0zpm9sb"
_PLACEHOLDER = "${ds_bigquery}"


def _all_datasource_uids(dashboard: dict) -> list[str]:
    uids: list[str] = []

    def _walk(panels):
        for panel in panels:
            ds = panel.get("datasource")
            if isinstance(ds, dict) and "uid" in ds:
                uids.append(ds["uid"])
            for target in panel.get("targets", []) or []:
                tds = target.get("datasource")
                if isinstance(tds, dict) and "uid" in tds:
                    uids.append(tds["uid"])
            if panel.get("panels"):
                _walk(panel["panels"])

    _walk(dashboard.get("panels", []))
    return uids


class TestBindDatasourceUid(unittest.TestCase):
    def _sample(self) -> dict:
        return {
            "templating": {"list": [
                {"name": "ds_bigquery", "type": "datasource",
                 "current": {"value": "BHAGA BigQuery"}},
                {"name": "date_from", "type": "textbox",
                 "current": {"value": "2026-01-01"}},
            ]},
            "panels": [
                {"id": 1, "title": "A",
                 "datasource": {"type": "grafana-bigquery-datasource", "uid": _PLACEHOLDER},
                 "targets": [{"refId": "A",
                              "datasource": {"type": "grafana-bigquery-datasource", "uid": _PLACEHOLDER},
                              "rawSql": "SELECT 1"}]},
                {"id": 2, "type": "row", "title": "Section",
                 "panels": [
                     {"id": 3, "title": "Nested",
                      "datasource": {"type": "grafana-bigquery-datasource", "uid": _PLACEHOLDER},
                      "targets": [{"refId": "A",
                                   "datasource": {"type": "grafana-bigquery-datasource", "uid": _PLACEHOLDER}}]},
                 ]},
            ],
        }

    def test_rewrites_all_refs_and_counts(self):
        dash = self._sample()
        n = bind_datasource_uid(dash, _REAL_UID)
        self.assertEqual(n, 4)  # 2 panel + 2 target refs (incl. nested)
        self.assertNotIn(_PLACEHOLDER, _all_datasource_uids(dash))
        self.assertTrue(all(u == _REAL_UID for u in _all_datasource_uids(dash)))

    def test_template_var_current_pinned_to_uid(self):
        dash = self._sample()
        bind_datasource_uid(dash, _REAL_UID)
        var = next(v for v in dash["templating"]["list"] if v["name"] == "ds_bigquery")
        self.assertEqual(var["current"]["value"], _REAL_UID)
        self.assertEqual(var["current"]["text"], "BHAGA BigQuery")

    def test_other_template_vars_untouched(self):
        dash = self._sample()
        bind_datasource_uid(dash, _REAL_UID)
        date_from = next(v for v in dash["templating"]["list"] if v["name"] == "date_from")
        self.assertEqual(date_from["current"]["value"], "2026-01-01")

    def test_idempotent(self):
        dash = self._sample()
        bind_datasource_uid(dash, _REAL_UID)
        second = bind_datasource_uid(dash, _REAL_UID)
        self.assertEqual(second, 0)  # nothing left to rewrite

    def test_real_dashboard_has_no_placeholder_after_bind(self):
        dash = json.loads(_DASHBOARD_JSON.read_text())
        n = bind_datasource_uid(dash, _REAL_UID)
        self.assertGreater(n, 0, "real dashboard should have ${ds_bigquery} refs to bind")
        self.assertNotIn(_PLACEHOLDER, _all_datasource_uids(dash))


if __name__ == "__main__":
    unittest.main()
