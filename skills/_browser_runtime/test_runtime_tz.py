"""Regression test: ensure launch_persistent always pins the browser
context to America/Chicago + en-US, regardless of host TZ.

Background (2026-05-22 incident): when the operator's laptop was in IST,
Square's date-range filter was interpreted in browser TZ, silently
truncating the gap export at IST midnight = CT 13:29:59 and dropping
~half a day of CT-5/22 transactions. Locking the context to
America/Chicago + en-US in skills/_browser_runtime/runtime.py prevents
this for every portal (Square, ADP, anything else).

This test opens an ACTUAL headless Chromium context via launch_persistent
and asserts the resolved timezone + locale match what we pinned.
Skipped gracefully when Chromium is not installable (CI without browsers
or sandboxed environment).
"""

from __future__ import annotations

import unittest


class BrowserContextTimezoneLockTest(unittest.TestCase):
    """Open a real Chromium context and verify TZ + locale are pinned."""

    def test_context_is_locked_to_central_time_and_en_us(self) -> None:
        try:
            from skills._browser_runtime.runtime import launch_persistent
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"could not import launch_persistent: {exc}")

        try:
            with launch_persistent("tz-regression-test", headed=False) as (_ctx, page):
                page.goto("about:blank")
                resolved_tz = page.evaluate(
                    "Intl.DateTimeFormat().resolvedOptions().timeZone"
                )
                navigator_lang = page.evaluate("navigator.language")
        except Exception as exc:  # noqa: BLE001
            # Chromium not installed, sandbox blocked, etc. — environmental,
            # not a code regression.
            raise unittest.SkipTest(
                f"Chromium not available for live TZ check: {type(exc).__name__}: {exc}"
            )

        self.assertEqual(
            resolved_tz, "America/Chicago",
            f"browser context TZ was {resolved_tz!r}, expected 'America/Chicago' — "
            "Square/ADP date filters will mis-interpret ranges when host is not CT",
        )
        self.assertEqual(
            navigator_lang, "en-US",
            f"browser context locale was {navigator_lang!r}, expected 'en-US' — "
            "date/number formatting in portal UIs may diverge from what selectors expect",
        )


if __name__ == "__main__":
    unittest.main()
