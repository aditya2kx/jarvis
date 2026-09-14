"""A recurring review anomaly must be reported once, not every night."""

from __future__ import annotations

import unittest
from unittest import mock

from agents.bhaga import notify


class TestPartitionAnomalies(unittest.TestCase):
    def test_splits_new_from_carried_over(self):
        new, carried = notify.partition_anomalies(["a", "b", "c"], ["b"])
        self.assertEqual(new, ["a", "c"])
        self.assertEqual(carried, ["b"])

    def test_everything_is_new_on_a_first_run(self):
        self.assertEqual(notify.partition_anomalies(["a"], []), (["a"], []))

    def test_preserves_input_order(self):
        new, _ = notify.partition_anomalies(["c", "a", "b"], ["a"])
        self.assertEqual(new, ["c", "b"])


class TestReviewAnomalyAlert(unittest.TestCase):
    def _run(self, anomalies, already):
        sent = []
        store = {"value": list(already)}
        fake_state = mock.Mock()
        fake_state.get_notify_state = lambda key: store["value"]
        fake_state.set_notify_state = lambda key, value: store.update(value=list(value))
        with mock.patch.dict("sys.modules", {"skills.bhaga_config.state_adapter": fake_state}), \
             mock.patch.object(notify, "_safe_send", lambda text: sent.append(text)):
            notify.review_anomaly_alert(anomalies)
        return sent, store["value"]

    def test_first_sighting_alerts(self):
        sent, remembered = self._run(["unparseable: post 12"], [])
        self.assertEqual(len(sent), 1)
        self.assertIn("post 12", sent[0])
        self.assertEqual(remembered, ["unparseable: post 12"])

    def test_identical_list_the_next_night_is_silent(self):
        sent, _ = self._run(["unparseable: post 12"], ["unparseable: post 12"])
        self.assertEqual(sent, [])

    def test_a_genuinely_new_anomaly_still_alerts(self):
        sent, remembered = self._run(
            ["unparseable: post 12", "5-star with no shift: post 19"],
            ["unparseable: post 12"],
        )
        self.assertEqual(len(sent), 1)
        self.assertIn("post 19", sent[0])
        self.assertNotIn("post 12", sent[0].split("still open")[0].split("•", 1)[1])
        self.assertIn("1 previously-reported", sent[0])
        self.assertEqual(len(remembered), 2)

    def test_empty_list_is_a_noop(self):
        sent, _ = self._run([], ["unparseable: post 12"])
        self.assertEqual(sent, [])

    def test_state_failure_falls_back_to_reporting(self):
        """Never silently drop an anomaly because the memory is unavailable."""
        sent = []
        fake_state = mock.Mock()
        fake_state.get_notify_state = mock.Mock(side_effect=RuntimeError("no firestore"))
        fake_state.set_notify_state = mock.Mock(side_effect=RuntimeError("no firestore"))
        with mock.patch.dict("sys.modules", {"skills.bhaga_config.state_adapter": fake_state}), \
             mock.patch.object(notify, "_safe_send", lambda text: sent.append(text)):
            notify.review_anomaly_alert(["unparseable: post 12"])
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
