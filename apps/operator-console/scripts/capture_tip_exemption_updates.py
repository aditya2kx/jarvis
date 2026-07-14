#!/usr/bin/env python3
"""Drive Tip Exemptions Update via Playwright for both unpaid periods; screenshot result.

For each unpaid period: edit a shift row (or queue orphan when no ADP shifts),
click Update, then screenshot the Exemptions table + status (not the full 90-row
shift grid — that buried the evidence).

Requires: BYPASS_IAP_EMAIL=… npm run dev

IMPORTANT: Use ``http://localhost:3000`` (not 127.0.0.1). Next.js 16 blocks
cross-origin ``/_next`` from 127.0.0.1 → SSR HTML without client hydration,
so Update never enables.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.bhaga.grafana.capture_screenshot import _get_github_token, upload_screenshot
from playwright.sync_api import sync_playwright

BASE = os.environ.get("CONSOLE_BASE_URL", "http://localhost:3000")
EDITOR = "[data-testid=tip-exemptions-editor]"
RESULT = "[data-testid=tip-exemptions-result]"

SCENARIOS = [
    ("/payroll?period=2026-06-29", "tip-exemptions-updated-unpaid-closed", "2026-07-10"),
    ("/payroll?period=2026-07-13", "tip-exemptions-updated-unpaid-current", "2026-07-14"),
]


def _dirty_via_shift(ed, note: str) -> tuple[str, str]:
    """Exempt first real shift row; return (date, emp)."""
    rows = ed.locator("table").first.locator("tbody tr")
    row = None
    for i in range(rows.count()):
        r = rows.nth(i)
        # Skip "No ADP shifts" placeholder (single td colspan)
        if r.locator("td").count() < 5:
            continue
        if r.locator("input[type='checkbox']").count() < 1:
            continue
        row = r
        break
    if row is None:
        raise RuntimeError("no editable shift rows")

    date = row.locator("td").nth(0).inner_text().strip()
    emp = row.locator("td").nth(1).inner_text().strip()
    exempt = row.locator("input[type='checkbox']").first
    if not exempt.is_checked():
        exempt.check()
    row.locator("input:not([type='checkbox']):not([disabled])").fill(note)
    return date, emp


def _dirty_via_orphan(ed, orphan_date: str, note: str) -> tuple[str, str]:
    orphan = ed.locator("div.rounded-md.border.p-3").filter(has_text="Orphan employee")
    if not orphan.count():
        raise RuntimeError("orphan panel missing (period not editable?)")
    sel = orphan.locator("select")
    emp = sel.locator("option").nth(0).inner_text().strip()
    sel.select_option(index=0)
    orphan.locator('input[type="date"]').fill(orphan_date)
    hhmm = orphan.locator('input[placeholder="HH:MM"]')
    hhmm.nth(0).fill("18:00")
    hhmm.nth(1).fill("18:30")
    # Note field is the last text input in the orphan panel
    orphan.locator('input:not([type="date"]):not([type="checkbox"])').last.fill(note)
    orphan.get_by_role("button", name="Queue orphan exemption").click()
    ed.get_by_text(re.compile(r"Queued orphan")).wait_for(timeout=5_000)
    return orphan_date, emp


def _result_png(page, ed) -> bytes:
    """Screenshot Exemptions table + status line (focused evidence)."""
    result = ed.locator(RESULT).first
    result.wait_for(state="visible", timeout=10_000)
    result.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    # Include status <p> below result — expand clip to editor bottom if needed
    status = ed.locator("p.text-xs.text-muted-foreground").last
    rbox = result.bounding_box()
    ebox = ed.bounding_box()
    if not rbox or not ebox:
        return result.screenshot(type="png")
    sbox = status.bounding_box() if status.count() else None
    bottom = max(rbox["y"] + rbox["height"], (sbox["y"] + sbox["height"]) if sbox else 0)
    clip = {
        "x": max(0, ebox["x"]),
        "y": max(0, rbox["y"] - 8),
        "width": ebox["width"],
        "height": min(bottom - rbox["y"] + 24, 900),
    }
    return page.screenshot(type="png", clip=clip)


def _run_one(page, path: str, orphan_date: str) -> bytes:
    page.goto(BASE + path, wait_until="networkidle", timeout=90_000)
    page.wait_for_timeout(2500)
    ed = page.locator(EDITOR).first
    ed.wait_for(state="visible", timeout=30_000)
    ed.scroll_into_view_if_needed()

    if ed.get_by_text("Historical — view only").count():
        raise RuntimeError(f"period not editable: {path}")

    # Unique note each run — Playwright fill of an identical value may not fire React onChange.
    import time as _time

    note = f"PR171 UI Update {orphan_date} {_time.strftime('%H%M%S')}"
    try:
        date, emp = _dirty_via_shift(ed, note)
        print(f"[ui-update]   shift path {date} / {emp} note={note!r}", file=sys.stderr)
    except RuntimeError:
        date, emp = _dirty_via_orphan(ed, orphan_date, note)
        print(f"[ui-update]   orphan path {date} / {emp} note={note!r}", file=sys.stderr)

    page.wait_for_timeout(300)
    update = ed.locator("button").filter(has_text=re.compile(r"^Update"))
    page.wait_for_function(
        """() => {
          const b = [...document.querySelectorAll('[data-testid=tip-exemptions-editor] button')]
            .find(el => /^Update/.test(el.textContent||''));
          return b && !b.disabled;
        }""",
        timeout=10_000,
    )
    update.click()
    try:
        ed.get_by_text(re.compile(r"Updated \d+ exemption")).wait_for(timeout=60_000)
    except Exception:
        fail = ed.get_by_text(re.compile(r"Update failed"))
        detail = fail.inner_text() if fail.count() else ed.inner_text()[:500]
        raise RuntimeError(f"Update did not succeed for {path}: {detail}") from None

    # Wait for Exemptions table to show the note (revalidatePath may refresh props)
    try:
        ed.locator(RESULT).get_by_text(note).wait_for(timeout=15_000)
    except Exception:
        print(f"[ui-update] WARN: note not yet in result table; capturing anyway", file=sys.stderr)

    page.wait_for_timeout(400)
    return _result_png(page, ed)


def main() -> None:
    if "127.0.0.1" in BASE:
        print(
            "WARNING: CONSOLE_BASE_URL uses 127.0.0.1 — Next.js may not hydrate. "
            "Prefer http://localhost:3000",
            file=sys.stderr,
        )
    token = _get_github_token()
    results: list[tuple[str, str]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1400})
        try:
            for path, label, tag in SCENARIOS:
                print(f"[ui-update] {label} {path}", file=sys.stderr)
                png = _run_one(page, path, tag)
                print(f"[ui-update]   → {len(png)} bytes", file=sys.stderr)
                url = upload_screenshot(png, label, token)
                print(f"[ui-update]   → {url}", file=sys.stderr)
                results.append((label, url))
        finally:
            browser.close()
    for label, url in results:
        print(f"{label}: {url}")


if __name__ == "__main__":
    main()
