"""Hidden TEMPUS anchors (Team Schedule, Timecards): retry from a reopened dashboard; keep the screenshot."""
from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest import mock

from skills.adp_run_automation import runner as r

PROFILE = {"adp_run": {"tenant_uuid": "t-1"}}


class FakePage:
    """`present_after` = how many anchor polls (counted across tries) miss before the anchor exists."""

    url = "https://runpayrollmain.adp.com/@t-1/v2/"

    def __init__(self, present_after: int | None):
        self.present_after, self.polls, self.gotos = present_after, 0, []

    def evaluate(self, js, *args):
        if js == r._NUDGE_TIME_JS:
            return None
        self.polls += 1
        return self.present_after is not None and self.polls > self.present_after

    def wait_for_timeout(self, _ms):
        pass

    def goto(self, url, **_kw):
        self.gotos.append(url)

    def wait_for_load_state(self, *_a, **_kw):
        pass


class ClickTempusAnchor(unittest.TestCase):
    def setUp(self):
        clock = iter(range(10_000))
        for p in (mock.patch.object(r, "_load_store_profile", return_value=PROFILE),
                  mock.patch.object(r.time, "monotonic", side_effect=lambda: next(clock))):
            p.start()
            self.addCleanup(p.stop)

    def test_first_try_clicks_without_reopening(self):
        page = FakePage(present_after=0)
        r._click_tempus_anchor(page, "TEMPUS_WEEKLY_SCHEDULE", label="Team Schedule", waits_s=(5, 5))
        self.assertEqual(page.gotos, [])

    def test_missing_on_first_render_reopens_dashboard_and_clicks(self):
        page = FakePage(present_after=6)
        r._click_tempus_anchor(page, "TEMPUS_WEEKLY_SCHEDULE", label="Team Schedule", waits_s=(5, 5))
        self.assertEqual(page.gotos, ["https://runpayrollmain.adp.com/@t-1/v2/"])

    def test_never_present_raises_with_evidence(self):
        page = FakePage(present_after=None)
        with mock.patch.object(r, "_raise_with_evidence", side_effect=RuntimeError("gone")) as rwe:
            with self.assertRaises(RuntimeError):
                r._click_tempus_anchor(page, "TEMPUS_TIMECARDS", label="Timecards", waits_s=(5, 5))
        self.assertIn("after 2 tries", rwe.call_args.kwargs["reason"])
        self.assertEqual(len(page.gotos), 1)


class RaiseWithEvidence(unittest.TestCase):
    def test_screenshot_goes_to_evidence_dir_and_gcs(self):
        shots = []
        page = mock.MagicMock(url="https://x")
        page.screenshot.side_effect = lambda path, full_page: shots.append(path)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(r, "EVIDENCE_DIR", pathlib.Path(d)), \
                mock.patch.object(r, "_upload_evidence_to_gcs", return_value="gs://b/2026-10-30/evidence/a.png"):
            with self.assertRaises(RuntimeError) as ctx:
                r._raise_with_evidence(page, store="palmetto", reason="anchor missing")
        self.assertTrue(shots[0].startswith(d))
        self.assertIn("Screenshot: gs://b/2026-10-30/evidence/a.png", str(ctx.exception))

    def test_failed_upload_keeps_typed_exception_and_local_path(self):
        class Throttled(Exception):
            pass

        page = mock.MagicMock(url="https://x")
        page.screenshot.side_effect = lambda path, full_page: pathlib.Path(path).write_bytes(b"png")
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(r, "EVIDENCE_DIR", pathlib.Path(d)), \
                mock.patch.object(r, "_upload_evidence_to_gcs", side_effect=OSError("gcs down")):
            with self.assertRaises(Throttled) as ctx:
                r._raise_with_evidence(page, store="palmetto", reason="sorry.adp.com", exc_factory=Throttled)
        self.assertIn(f"Screenshot: {d}", str(ctx.exception))

    def test_failed_screenshot_still_raises_and_says_so(self):
        page = mock.MagicMock(url="https://x")
        page.screenshot.side_effect = OSError("closed")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(r, "EVIDENCE_DIR", pathlib.Path(d)):
            with self.assertRaises(RuntimeError) as ctx:
                r._raise_with_evidence(page, store="palmetto", reason="x")
        self.assertIn("Screenshot: none (OSError)", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
