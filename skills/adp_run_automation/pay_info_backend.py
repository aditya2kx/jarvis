#!/usr/bin/env python3
"""ADP People → Payroll info → Hourly pay rate refresh (Issue #213 / #251).

Earnings & Hours only has rates after a paycheck. Payroll info is the live
setup rate (raises land here before the next check). The nightly ADP bundle
scrapes **all recent punchers** (not just NULL gaps), MERGEs ``wage_rate_dollars``,
and preserves existing OT / salaried flags from earnings. Per-employee scrape
failures Slack a warning and do **not** fail Timecard/tips.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import time
from typing import Iterable, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import project_dir

_PROJECT = pathlib.Path(project_dir())
DOWNLOADS_DIR = _PROJECT / "extracted" / "downloads"
SELECTORS_PATH = (
    _PROJECT / "skills" / "adp_run_automation" / "selectors" / "pay_info.json"
)

_HOURLY_RATE_RE = re.compile(
    r"Hourly\s+pay\s+rate\s*\$?\s*([\d,]+\.\d{2,4})",
    re.IGNORECASE,
)
_ADDED_ON_RE = re.compile(
    r"Added\s+on\s+(\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
# People home (2026-08) uses "Search for an employee's name"; older Directory
# used "Search people". Both must match — Issue #213 Elizabeth gap was this.
_PEOPLE_SEARCH_PLACEHOLDER_RE = re.compile(
    r"Search\s+(people|for an employee)",
    re.IGNORECASE,
)
_RATE_FLOOR_DOLLARS = 7.25    # federal minimum; no Palmetto rate is legitimately below this
_RATE_CEILING_DOLLARS = 100.0


def load_selectors() -> dict:
    if SELECTORS_PATH.exists():
        return json.loads(SELECTORS_PATH.read_text())
    return {}


def directory_search_name(canonical: str) -> str:
    """Map Timecard-style 'Last First' → Directory 'Last, First' for search."""
    name = " ".join(str(canonical or "").split())
    if not name:
        return ""
    if "," in name:
        return name
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]}, {' '.join(parts[1:])}"
    return name


def accepted_directory_names(canonical: str, aliases: Optional[dict] = None) -> list[str]:
    """Directory spellings of every alias key that resolves to ``canonical``."""
    search = directory_search_name(canonical)
    out: list[str] = []
    for raw, canon in (aliases or {}).items():
        if canon != canonical:
            continue
        name = directory_search_name(raw)
        if name and _name_key(name) != _name_key(search) and name not in out:
            out.append(name)
    return out


def parse_hourly_pay_rate(body_text: str, *, input_values: Optional[list[str]] = None) -> dict:
    """Extract Hourly pay rate (+ optional Added on) from Payroll info body/inputs."""
    candidates = list(input_values or [])
    if body_text:
        candidates.append(body_text)
    rate = None
    for blob in candidates:
        m = _HOURLY_RATE_RE.search(blob)
        if m:
            rate = float(m.group(1).replace(",", ""))
            break
        # Anchored: the blob must be *only* a number, which is true of an
        # <input> whose whole value is the rate and of nothing else. An
        # unanchored search here took the first decimal anywhere on the page and
        # on 2026-09-15 handed three employees the same 1.25 page artifact.
        if re.fullmatch(r"\$?\s*([\d,]+\.\d{2,4})\s*", blob):
            rate = float(blob.strip().lstrip("$").replace(",", ""))
            break
    if rate is None and body_text:
        m3 = re.search(
            r"Hourly\s+pay\s+rate.{0,40}?\$?\s*([\d,]+\.\d{2,4})",
            body_text,
            re.IGNORECASE | re.DOTALL,
        )
        if m3:
            rate = float(m3.group(1).replace(",", ""))
    if rate is None:
        raise ValueError("Hourly pay rate not found on Payroll info page")
    added = None
    if body_text:
        am = _ADDED_ON_RE.search(body_text)
        if am:
            mm, dd, yyyy = am.group(1).split("/")
            added = f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
    return {"wage_rate_dollars": rate, "added_on": added}


_CARD_RATE_RE = re.compile(r"\$?\s*([\d,]+\.\d{2,4})")
_CARD_DATE_RE = re.compile(
    r"(?:Added|Last\s+changed)\s+on\s+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE,
)


def parse_pay_rate_cards(cards: list[dict]) -> Optional[dict]:
    """Pick the base rate from the 'Pay rates' cards (ADP layout since 2026-09-29).

    ``cards`` rows: ``{"rate_text": "$15.2500", "label": "Default rate",
    "date_text": "Added on 02/16/2026"}``. Most employees carry a second,
    unlabelled, undated card — the rate-2 premium. It must never become the
    base rate (invariant 12), so the ``Default rate`` card wins; with no single
    default card the lowest rate is taken and ``default_card`` is False so the
    caller can leave a breadcrumb.

    Returns ``{"wage_rate_dollars", "added_on", "default_card", "cards"}`` or
    None when no card has rendered its rate yet.
    """
    parsed = []
    for card in cards:
        m = _CARD_RATE_RE.fullmatch((card.get("rate_text") or "").strip())
        if not m:
            continue
        dm = _CARD_DATE_RE.search(card.get("date_text") or "")
        added = None
        if dm:
            mm, dd, yyyy = dm.group(1).split("/")
            added = f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
        parsed.append({
            "rate": float(m.group(1).replace(",", "")),
            "default": bool(re.search(r"\bdefault\s+rate\b", card.get("label") or "", re.I)),
            "added_on": added,
        })
    if not parsed:
        return None
    defaults = [p for p in parsed if p["default"]]
    pick = defaults[0] if len(defaults) == 1 else min(defaults or parsed, key=lambda p: p["rate"])
    return {
        "wage_rate_dollars": pick["rate"],
        "added_on": pick["added_on"],
        "default_card": len(defaults) == 1,
        "cards": len(parsed),
    }


def parse_payroll_info(
    cards: list[dict], body_text: str, input_values: Optional[list[str]] = None,
) -> Optional[dict]:
    """Rate from the Pay rates cards, else the pre-2026-09-29 label; None if blank."""
    parsed = parse_pay_rate_cards(cards)
    if parsed:
        return {**parsed, "rate_layout": "cards"}
    try:
        return {**parse_hourly_pay_rate(body_text, input_values=input_values),
                "rate_layout": "legacy"}
    except ValueError:
        return None


def rate_record(
    employee_name: str,
    *,
    wage_rate_dollars: float,
    added_on: Optional[str] = None,
    excluded: bool = False,
) -> dict:
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    history = []
    if added_on:
        history.append({"check_date": added_on, "rate": wage_rate_dollars, "source": "pay_info"})
    return {
        "employee_id": employee_name,
        "employee_name": employee_name,
        "wage_rate_dollars": wage_rate_dollars,
        "ot_rate_dollars": None,
        "is_salaried": False,
        "multi_rate": False,
        "rate_history": history,
        "ot_rate_history": [],
        "excluded_from_labor_pct": excluded,
        "raw_employee_names": [employee_name],
        "rate_source": "pay_info",
        "added_on": added_on,
        "scraped_at_utc": now,
    }


def dismiss_blocking_modals(page) -> bool:
    """Dismiss ADP's Session Timeout dialog. Returns True if one was dismissed.

    Found live on 2026-09-13: after an idle stretch ADP renders
    ``div.message-box-outer`` — position:fixed, z-index 20000, covering the whole
    viewport — reading "Your session is about to be timed out. Click OK now to
    continue working, or Cancel to sign out." It intercepts pointer events, so
    every click fails with the exact signature we had been seeing nightly:
    ``TimeoutError: Locator.click: Timeout 10000ms exceeded``.

    Two things make it worth its own handler. Escape does not close it — it is a
    custom Ok/Cancel dialog, and ``_close_overlays`` only pressed Escape. And left
    unanswered it eventually signs the session out, so one employee's timeout
    poisons every employee after them in the loop, which is why the failures
    arrived in clusters rather than singly.

    Clicks **Ok** to extend the session. Never Cancel — Cancel signs out.
    """
    try:
        return bool(page.evaluate(
            """() => {
              const box = document.querySelector('div.message-box-outer');
              if (!box || box.offsetParent === null) return false;
              const btns = [...box.querySelectorAll('button,sdf-button,a')];
              // Match Ok exactly. Never Cancel: it signs the session out.
              const ok = btns.find(b => /^\\s*ok\\s*$/i.test(b.innerText || ''));
              if (!ok) return false;
              ok.click();
              return true;
            }"""
        ))
    except Exception:  # noqa: BLE001
        return False


def _close_overlays(page) -> None:
    if dismiss_blocking_modals(page):
        print("[pay_info] dismissed ADP Session Timeout modal (clicked Ok)")
        page.wait_for_timeout(500)
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    except Exception:  # noqa: BLE001
        pass


def _click_through_modals(locator, *, page, timeout: int = 10_000) -> None:
    """Click ``locator``, retrying once after dismissing a blocking modal.

    The Session Timeout dialog can appear between any two actions, so a single
    up-front check is not enough — the retry has to happen at the click itself.
    """
    try:
        locator.click(force=True, timeout=timeout)
        return
    except Exception:
        if not dismiss_blocking_modals(page):
            raise
        print("[pay_info] click blocked by Session Timeout modal — dismissed, retrying")
        page.wait_for_timeout(500)
        locator.click(force=True, timeout=timeout)


def clear_directory_status_filter(page) -> bool:
    """Include Terminated + Leave of absence in the People Directory list.

    The Status filter defaults to Active only, so terminated employees are
    structurally invisible to a Directory search no matter how long it waits —
    this is why ``Flores, Juan`` failed every single night rather than
    intermittently. Enabling the other statuses took the roster from 14 to 20 in
    the 2026-09-13 spike.

    Employment status is the wrong filter for this job regardless: a terminated
    employee still has hours in their final pay period, and hours are what
    require a rate. ``Flores, Juan`` and ``Urrutia, Emely`` are both terminated
    and both appear in the last-60-day punch roster.

    Returns True when the Terminated badge is showing afterwards.
    """
    try:
        trigger = page.locator('[data-test-id="filter-button"]').first
        if not trigger.count():
            return False
        _click_through_modals(trigger, page=page, timeout=8_000)
        page.wait_for_timeout(1200)
        states = page.evaluate(
            """() => [...document.querySelectorAll('[data-test-id^="aeed-filter-checkbox-"]')]
              .map(b => ({test_id: b.getAttribute('data-test-id'),
                          aria_checked: b.getAttribute('aria-checked')}))"""
        ) or []
        clicks = status_filter_clicks(states)
        for tid in clicks:
            page.locator(f'[data-test-id="{tid}"]').first.click(timeout=5_000)
            page.wait_for_timeout(500)
        # No Apply button: Escape closes the pane and the list re-queries.
        page.keyboard.press("Escape")
        page.wait_for_timeout(1500)
        badges = _directory_status_badges(page)
        print(f"[pay_info] directory status filter badges={badges} clicked={len(clicks)}")
        if "Terminated" not in badges:
            print(f"[pay_info] BREADCRUMB directory_status_filter_unchanged badges={badges}")
        return "Terminated" in badges
    except Exception as exc:  # noqa: BLE001
        print(f"[pay_info] status-filter clear failed (non-fatal): "
              f"{type(exc).__name__}: {exc}")
        return False


_FILTER_CHECKBOX_PREFIX = "aeed-filter-checkbox-"
_FILTER_BADGE_PREFIX = "aeed-filter-badge-"
_WANTED_STATUSES = ("Terminated", "Leave of absence")


def status_filter_clicks(states: list[dict]) -> list[str]:
    """data-test-ids of the wanted status checkboxes that are not yet ticked.

    ``states`` rows: ``{"test_id", "aria_checked"}``. sdf-checkbox always
    carries a ``checked`` attribute (``"true"``/``"false"``), so the old
    ``hasAttribute('checked')`` read every box as ticked and never clicked one
    (four employees invisible every night until 2026-10). ``aria-checked`` is
    the only reliable read.
    """
    wanted = {f"{_FILTER_CHECKBOX_PREFIX}{s}" for s in _WANTED_STATUSES}
    return [
        s["test_id"] for s in states
        if s.get("test_id") in wanted and (s.get("aria_checked") or "").lower() != "true"
    ]


def _directory_status_badges(page) -> list[str]:
    """Statuses currently applied to the Directory list (badge text is empty)."""
    try:
        ids = page.evaluate(
            f"""() => [...document.querySelectorAll('[data-test-id^="{_FILTER_BADGE_PREFIX}"]')]
              .map(t => t.getAttribute('data-test-id'))"""
        ) or []
    except Exception:  # noqa: BLE001
        return []
    return [i[len(_FILTER_BADGE_PREFIX):] for i in ids if i.startswith(_FILTER_BADGE_PREFIX)]


def _open_people_home(page) -> None:
    page.locator('[data-test-id="People-btn"]').first.click(force=True)
    page.wait_for_timeout(3500)
    # 2026-08 People home is a hub (Directory / HR / Time Management), not the
    # old in-page directory. Directory has the searchable roster.
    directory = page.get_by_role("button", name=re.compile(r"^Directory$", re.I))
    if directory.count() == 0:
        directory = page.get_by_text(re.compile(r"^Directory$", re.I))
    if directory.count():
        try:
            _click_through_modals(directory.first, page=page, timeout=8_000)
            page.wait_for_timeout(3000)
        except Exception:  # noqa: BLE001
            pass
    clear_directory_status_filter(page)


def _people_search_box(page):
    """Locate People/Directory search; pierce sdf-input shadow to a native input."""
    host = page.locator('[data-test-id="aeed-desktop-search-input"]')
    try:
        if host.count():
            inner = host.locator("input").first
            inner.wait_for(state="visible", timeout=8_000)
            return inner
    except Exception:
        pass
    by_ph = page.get_by_placeholder(_PEOPLE_SEARCH_PLACEHOLDER_RE)
    try:
        by_ph.first.wait_for(state="visible", timeout=8_000)
        tag = (by_ph.first.evaluate("el => el.tagName") or "").lower()
        if tag == "sdf-input":
            inner = by_ph.first.locator("input").first
            inner.wait_for(state="visible", timeout=8_000)
            return inner
        return by_ph.first
    except Exception:
        pass
    fallback = page.locator(
        'input[placeholder*="Search people" i], '
        'input[aria-label*="Search people" i], '
        'input[placeholder*="employee" i], '
        'input[placeholder*="Search for an employee" i]'
    )
    fallback.first.wait_for(state="visible", timeout=20_000)
    return fallback.first


class AmbiguousEmployeeError(RuntimeError):
    """More than one Directory record could be the person we searched for."""


def _name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def _only_active(rows: list[tuple[str, str]]) -> Optional[str]:
    active = [n for n, status in rows if status.casefold() == "active"]
    return active[0] if len(active) == 1 else None


def select_directory_match(
    candidates: list,
    search_name: str,
    *,
    accepted_names: Iterable[str] = (),
) -> str:
    """Pick the one Directory row that IS ``search_name``, or refuse.

    Exact match only. ``Johnson, Dolce`` and ``Johnson, Dolce J`` are two
    different Directory records, and our punch roster carries the first — a
    substring or first-hit match would attach one record's wage rate to the
    other. A missing rate is recoverable from the earnings report; a wrong rate
    is silently wrong pay.

    ``accepted_names`` are other spellings the alias table maps to the SAME
    canonical employee. With no exact hit on ``search_name``, exactly one hit
    on an accepted spelling is taken. When the exact name and an accepted
    spelling are both listed, they are two records of one person and only one
    carries the live rate: the single ``Active`` one is taken (a rehire leaves
    the old record Terminated — Dolce, $15.25 Terminated vs $18 Active), and
    with no single Active record the scrape refuses. An inactive exact match
    beside an Active longer spelling with no alias between them also refuses.

    ``candidates`` rows are ``{"name", "status"}`` dicts or bare names.
    """
    rows = [
        (c["name"], c.get("status") or "") if isinstance(c, dict) else (c, "")
        for c in candidates
    ]
    norm = _name_key(search_name)
    exact = [r for r in rows if _name_key(r[0]) == norm]
    if len(exact) > 1:
        raise AmbiguousEmployeeError(
            f"{search_name!r} matches {len(exact)} Directory records exactly — "
            f"cannot tell them apart by name: {[n for n, _ in exact]}"
        )
    accepted = {_name_key(n) for n in accepted_names} - {norm}
    via_alias = [r for r in rows if _name_key(r[0]) in accepted]
    if (exact and via_alias) or len(via_alias) > 1:
        same_person = exact + via_alias
        picked = _only_active(same_person)
        if picked:
            print(
                f"[pay_info] BREADCRUMB directory_active_wins name={search_name!r} "
                f"picked={picked!r} others={[n for n, _ in same_person if n != picked]}"
            )
            return picked
        raise AmbiguousEmployeeError(
            f"{search_name!r} has {len(same_person)} Directory records for one "
            f"person and not exactly one is Active: {same_person} — refusing to pick."
        )
    if len(exact) == 1:
        name, status = exact[0]
        # With Terminated rows listed, an inactive exact match can be the old
        # record of someone rehired under a longer spelling. Without an alias
        # tying the two together we cannot tell, and taking the old record's
        # rate (Dolce: $15.25 over $18) passes every plausibility check.
        rehired = [
            n for n, s in rows
            if n != name and norm in _name_key(n) and s.casefold() == "active"
        ]
        if status and status.casefold() != "active" and rehired:
            raise AmbiguousEmployeeError(
                f"{search_name!r} is {status} and Active record(s) {rehired} "
                f"share the name with no alias linking them — refusing to pick."
            )
        return name
    if len(via_alias) == 1:
        return via_alias[0][0]
    near = [n for n, _ in rows if norm in _name_key(n)]
    if near:
        raise AmbiguousEmployeeError(
            f"no Directory record is exactly {search_name!r}; closest are {near}. "
            f"Refusing to guess — a wrong match writes the wrong wage rate."
        )
    raise LookupError(f"{search_name!r} not found in the Directory")


def _directory_candidates(page) -> list[dict]:
    """``{"name", "status"}`` of the currently-listed Directory rows.

    Rows carry ``aria-label="Go to the profile page for <Name>"`` and
    ``data-test-id="active-name-cell-button"``; neither exposes an ADP associate
    ID, so the name is all we have to match on today (see the identity note in
    docs/plans/payroll-pipeline-robustness.md). Status (``Active`` /
    ``Terminated`` / ``Leave of absence``) is the row's ``-col-Status`` cell.
    """
    try:
        return page.evaluate(
            """() => {
              const out = [];
              const nodes = document.querySelectorAll(
                '[aria-label^="Go to the profile page for"]'
              );
              for (const n of nodes) {
                const m = (n.getAttribute('aria-label') || '')
                  .replace(/^Go to the profile page for\\s*/i, '').trim();
                if (!m) continue;
                const row = n.closest('[data-test-id^="table-row-"]');
                const cell = row && row.querySelector('[data-test-id$="-col-Status"]');
                const status = cell ? (cell.innerText || cell.textContent || '').trim() : '';
                out.push({name: m, status});
              }
              return out;
            }"""
        ) or []
    except Exception:  # noqa: BLE001
        return []


def directory_roster(page, *, max_scrolls: int = 30) -> list[dict]:
    """Every Directory row (all statuses) as ``{"name", "status"}``. Read-only.

    The list is virtualized, so scroll the last row into view until the set of
    names stops growing.
    """
    _open_people_home(page)
    seen: dict[str, str] = {}
    for _ in range(max_scrolls):
        before = len(seen)
        for c in _directory_candidates(page):
            seen[c["name"]] = c.get("status") or ""
        if len(seen) == before and before:
            break
        page.evaluate(
            """() => { const n = document.querySelectorAll('[aria-label^="Go to the profile page for"]');
                       if (n.length) n[n.length - 1].scrollIntoView({block: 'end'}); }"""
        )
        page.wait_for_timeout(800)
    rows = [{"name": n, "status": s} for n, s in sorted(seen.items())]
    print(f"[pay_info] directory_roster n={len(rows)} "
          f"{json.dumps({r['name']: r['status'] for r in rows})}")
    return rows


def _wait_for_directory_results(page, needle: str, *, timeout_ms: int = 15_000) -> None:
    """Wait until the Directory list reflects the search, then settle.

    Polls for a row matching ``needle`` instead of sleeping a fixed interval.
    Returns quietly on timeout — the caller's own locator produces the better
    error message, and a slow-but-present list still works.
    """
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if any(needle.casefold() in c["name"].casefold() for c in _directory_candidates(page)):
            page.wait_for_timeout(400)  # let row handlers bind
            return
        if dismiss_blocking_modals(page):
            print("[pay_info] dismissed Session Timeout modal while awaiting results")
        page.wait_for_timeout(300)


PAYROLL_INFO_ATTEMPTS = 3
_PAYROLL_READY_BUDGET_S = 10.0

# Read-only: collects text, never clicks (the cards carry an Edit rate button).
_PAYROLL_CARDS_JS = """() => {
  const txt = (root, sel) => {
    const el = root.querySelector(sel);
    return el ? (el.innerText || el.textContent || '').trim() : '';
  };
  const cards = [...document.querySelectorAll('[data-test-id^="pay-rate-card-"]')]
    .map(c => ({
      rate_text: txt(c, '[data-test-id="current-pay-rate"]'),
      label: txt(c, '[data-test-id="default-rate-label"]'),
      date_text: txt(c, '[data-test-id="default-rate-date-message"]'),
    }));
  return {cards, heading_undefined: !!document.querySelector('[data-test-id="undefined"]')};
}"""


def scrape_one_pay_info(
    page,
    canonical_name: str,
    *,
    dashboard_url: str,
    accepted_names: Iterable[str] = (),
) -> dict:
    """Directory → profile → Payroll info; return rate fields.

    The Payroll info pane sometimes renders only an ``undefined`` heading and
    never fills in (Huynh, 2026-10-01 spike). Waiting, toggling to Tax info and
    reloading all failed to recover it — the profile URL is the SPA root, so a
    reload lands on the dashboard — while reopening the profile from the
    Directory recovered it every time. So each attempt starts from the Directory.
    """
    for attempt in range(1, PAYROLL_INFO_ATTEMPTS + 1):
        profile_name = _open_profile(
            page, canonical_name, dashboard_url=dashboard_url, accepted_names=accepted_names,
        )
        pane = _read_payroll_info(page)
        parsed = parse_payroll_info(pane["cards"], pane["text"], pane["inputs"])
        if parsed:
            break
        print(
            f"[pay_info] BREADCRUMB payroll_info_blank name={canonical_name!r} "
            f"attempt={attempt}/{PAYROLL_INFO_ATTEMPTS} "
            f"heading_undefined={pane['heading_undefined']} cards={len(pane['cards'])}"
        )
    else:
        raise ValueError(
            f"Payroll info blank after {PAYROLL_INFO_ATTEMPTS} attempts "
            f"(no pay-rate card, no Hourly pay rate)"
        )
    if parsed.get("rate_layout") == "cards" and not parsed.get("default_card"):
        print(
            f"[pay_info] BREADCRUMB pay_rate_card_no_default name={canonical_name!r} "
            f"cards={parsed.get('cards')} took_lowest={parsed['wage_rate_dollars']}"
        )
    return {
        "employee_name": canonical_name,
        "search_name": profile_name,
        **parsed,
        "body_excerpt": pane["text"][:500],
        "inputs": pane["inputs"][:20],
    }


def _open_profile(
    page,
    canonical_name: str,
    *,
    dashboard_url: str,
    accepted_names: Iterable[str] = (),
) -> str:
    """Dashboard → Directory → search → the one matching profile. Returns its name."""
    search_name = directory_search_name(canonical_name)
    profile_name = search_name
    last_name = search_name.split(",")[0].strip()

    page.goto(dashboard_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1500)
    _close_overlays(page)

    _open_people_home(page)

    search = _people_search_box(page)
    search.click(force=True)
    search.fill(last_name)
    # Wait for the roster to actually render rather than sleeping a fixed 2.5 s
    # and hoping. The old sleep raced ADP's async list on a slow response, which
    # is indistinguishable at the call site from "this person does not exist".
    _wait_for_directory_results(page, last_name)

    candidates = _directory_candidates(page)
    if not candidates:
        raise LookupError(
            f"{search_name!r} not in Directory "
            f"(status badges={_directory_status_badges(page)})"
        )
    # Guard the live collision: the Directory holds both `Johnson, Dolce`
    # (Terminated) and `Johnson, Dolce J` (Active). Now that the status
    # filter is cleared, a substring match would silently pick the wrong
    # person, and a wrong wage rate is worse than a missing one.
    profile_name = select_directory_match(
        candidates, search_name, accepted_names=accepted_names
    )

    mpi = page.get_by_text("Manage pay info", exact=False)
    if mpi.count():
        try:
            mpi.first.click(force=True, timeout=8_000)
        except Exception:
            page.evaluate(
                """() => {
                  const el = [...document.querySelectorAll('sdf-link,a,button,span')]
                    .find(e => /Manage pay info/i.test((e.innerText || '').trim()));
                  if (el) el.click();
                }"""
            )
        page.wait_for_timeout(5000)
    else:
        row = page.locator(f'[aria-label="Go to the profile page for {profile_name}"]')
        _click_through_modals(row.first, page=page, timeout=10_000)
        page.wait_for_timeout(2500)
    return profile_name


def _read_payroll_info(page, *, budget_s: float = _PAYROLL_READY_BUDGET_S) -> dict:
    """Open the Payroll info tab and poll until a pay-rate card shows its rate.

    "Ready" is a non-empty ``current-pay-rate``, not a card being present: a
    card can render for ~0.5 s before its rate text (Pascone, 2026-10-01).
    Returns ``{"cards", "heading_undefined", "text", "inputs"}``.
    """
    page.evaluate(
        """() => {
          const a = document.getElementById('EMPLOYEE_PAYROLL');
          if (a) { a.click(); return; }
          const hit = [...document.querySelectorAll('a,button,div,span,li')]
            .find(e => (e.innerText || '').trim() === 'Payroll info');
          if (hit) hit.click();
        }"""
    )
    deadline = time.time() + budget_s
    state = {"cards": [], "heading_undefined": False}
    while True:
        state = page.evaluate(_PAYROLL_CARDS_JS) or state
        if parse_pay_rate_cards(state["cards"]) or time.time() >= deadline:
            break
        if dismiss_blocking_modals(page):
            print("[pay_info] dismissed Session Timeout modal while awaiting Payroll info")
        page.wait_for_timeout(500)
    try:
        page.evaluate(
            """() => {
              const l = document.querySelector('[data-test-id="pay-rates-list"]');
              if (l) l.scrollIntoView();
            }"""
        )
    except Exception:  # noqa: BLE001
        pass

    inputs = page.evaluate(
        """() => {
          const out = [];
          const pushEl = (el) => {
            const inner = (el.shadowRoot && el.shadowRoot.querySelector('input')) || null;
            const v = (
              (inner && inner.value) || el.value || el.getAttribute('value') || ''
            ).trim();
            const a = (
              el.getAttribute('aria-label') || el.getAttribute('name')
              || el.getAttribute('placeholder') || el.getAttribute('label') || ''
            ).trim();
            const label = (el.labels && el.labels[0] && el.labels[0].innerText) || '';
            if (v || /rate|hour|pay/i.test(a + label)) {
              out.push((a || label || 'input') + ' ' + v);
            }
          };
          for (const el of document.querySelectorAll('input, sdf-input')) pushEl(el);
          return out;
        }"""
    ) or []
    text = page.evaluate(
        """() => {
          const chunks = [];
          const walk = (node) => {
            if (!node) return;
            if (node.nodeType === 3) {
              const t = (node.textContent || '').trim();
              if (t) chunks.push(t);
            }
            if (node.shadowRoot) walk(node.shadowRoot);
            const kids = node.childNodes || [];
            for (let i = 0; i < kids.length; i++) walk(kids[i]);
          };
          walk(document.body);
          return chunks.join(' ').replace(/\\s+/g, ' ').trim();
        }"""
    ) or ""
    return {**state, "text": text, "inputs": inputs}


def _capture_pay_info_failure(page, name: str) -> list[str]:
    """Save screenshot + DOM for a failed scrape to durable GCS evidence.

    Previously this wrote a PNG to ``~/.bhaga/state/screenshots`` — a path that
    does not survive a Cloud Run execution, which is why no evidence exists for
    any failure since 2026-08-24 despite the code appearing to capture it.
    Routing through the shared ``_capture_failure_evidence`` puts the artifacts
    in ``gs://<cache>/<date>/evidence/`` with a greppable breadcrumb, so a
    nightly failure is diagnosable from logs + GCS without reproducing it.
    """
    try:
        from skills._browser_runtime.runtime import (  # noqa: PLC0415
            _capture_failure_evidence,
        )

        slug = name.replace(",", "").replace(" ", "_")
        return _capture_failure_evidence(page, portal=f"adp-pay-info-{slug}")
    except Exception as exc:  # noqa: BLE001
        print(f"[pay_info] evidence capture failed: {type(exc).__name__}: {exc}")
        return []


def scrape_pay_info_rates(
    page,
    names: list[str],
    *,
    dashboard_url: str,
    excluded: Optional[set[str]] = None,
    aliases: Optional[dict] = None,
) -> tuple[list[dict], dict[str, str]]:
    """Scrape Payroll info rates for names. Per-employee failures are soft."""
    excluded = excluded or set()
    out: list[dict] = []
    errors: dict[str, str] = {}
    for name in names:
        try:
            raw = scrape_one_pay_info(
                page, name, dashboard_url=dashboard_url,
                accepted_names=accepted_directory_names(name, aliases),
            )
            out.append(
                rate_record(
                    name,
                    wage_rate_dollars=raw["wage_rate_dollars"],
                    added_on=raw.get("added_on"),
                    excluded=name in excluded,
                )
            )
            print(
                f"[pay_info] OK {name} → ${raw['wage_rate_dollars']:.4f}"
                f" (added_on={raw.get('added_on')}"
                f" rate_layout={raw.get('rate_layout')})"
            )
        except Exception as exc:  # noqa: BLE001
            errors[name] = f"{type(exc).__name__}: {exc}"
            print(f"[pay_info] FAIL {name}: {errors[name]}")
            _capture_pay_info_failure(page, name)
            # The modal poisons everyone after it in the loop, so clear it now
            # rather than letting the next employee inherit the same block.
            if dismiss_blocking_modals(page):
                print("[pay_info] dismissed Session Timeout modal after failure")
    if errors:
        print(f"[pay_info] {len(errors)} failure(s): {errors}")
    return out, errors


def write_pay_info_json(
    rates: list[dict],
    *,
    store: str = "palmetto",
    errors: Optional[dict[str, str]] = None,
    attempted: Optional[list[str]] = None,
    directory: Optional[list[dict]] = None,
) -> pathlib.Path:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    path = DOWNLOADS_DIR / f"PayInfoRates-{datetime.date.today().isoformat()}.json"
    path.write_text(
        json.dumps(
            {
                "scraped_at_utc": datetime.datetime.utcnow().isoformat() + "Z",
                "store": store,
                "rates": rates,
                "errors": errors or {},
                "attempted": attempted or [r.get("employee_name") for r in rates],
                "directory": directory or [],
            },
            indent=2,
        )
    )
    print(f"[pay_info] wrote {path} ({len(rates)} rates)")
    return path


def puncher_names_from_bq(*, days: int = 60) -> list[str]:
    """All punchers in the last N days (for nightly rate refresh)."""
    from google.cloud import bigquery  # noqa: PLC0415

    from core.datastore import fq, get_client  # noqa: PLC0415

    client = get_client()
    if client is None:
        raise RuntimeError("BigQuery client unavailable — cannot list punchers")
    sql = f"""
      SELECT DISTINCT
        COALESCE(NULLIF(TRIM(canonical_name), ''), employee_id) AS employee_name
      FROM {fq("adp_punches")}
      WHERE date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
        AND COALESCE(NULLIF(TRIM(canonical_name), ''), employee_id) IS NOT NULL
      ORDER BY 1
    """
    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("days", "INT64", days)]
        ),
    )
    return [row.employee_name for row in job.result() if row.employee_name]


def gap_names_from_bq(*, days: int = 60) -> list[str]:
    """Punchers in the last N days with missing/null wage_rate_dollars."""
    from google.cloud import bigquery  # noqa: PLC0415

    from core.datastore import fq, get_client  # noqa: PLC0415

    client = get_client()
    if client is None:
        raise RuntimeError("BigQuery client unavailable — cannot resolve wage gaps")
    sql = f"""
      WITH punchers AS (
        SELECT DISTINCT
          COALESCE(NULLIF(TRIM(canonical_name), ''), employee_id) AS employee_name
        FROM {fq("adp_punches")}
        WHERE date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
      ),
      rates AS (
        SELECT employee_id, canonical_name, wage_rate_dollars
        FROM {fq("adp_wage_rates")}
      )
      SELECT p.employee_name
      FROM punchers p
      LEFT JOIN rates r
        ON p.employee_name = r.employee_id
        OR p.employee_name = r.canonical_name
      WHERE r.wage_rate_dollars IS NULL
      ORDER BY 1
    """
    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("days", "INT64", days)]
        ),
    )
    return [row.employee_name for row in job.result() if row.employee_name]


def existing_rates_bq() -> dict[str, dict]:
    """Map canonical_name/employee_id → current adp_wage_rates fields."""
    from core.datastore import fq, get_client  # noqa: PLC0415

    client = get_client()
    if client is None:
        return {}
    sql = f"""
      SELECT employee_id, canonical_name, wage_rate_dollars, ot_rate_dollars,
             is_salaried, multi_rate, excluded_from_labor_pct, rate_source
      FROM {fq("adp_wage_rates")}
    """
    out: dict[str, dict] = {}
    for row in client.query(sql).result():
        rec = dict(row)
        for key in (rec.get("canonical_name"), rec.get("employee_id")):
            if key:
                out[str(key)] = rec
    return out


def prepare_pay_info_writes(
    rates: list[dict],
    existing: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    """MERGE payload + rate-change audit. Preserve earnings OT / salaried flags."""
    fills: list[dict] = []
    changes: list[dict] = []
    for rec in rates:
        wage = rec.get("wage_rate_dollars")
        if wage is None:
            continue
        key = rec.get("employee_name") or rec.get("employee_id") or ""
        prev = existing.get(key) or {}
        merged = dict(rec)
        inc_ot = rec.get("ot_rate_dollars")
        prev_ot = prev.get("ot_rate_dollars")
        if (inc_ot is None or inc_ot == 0) and prev_ot:
            merged["ot_rate_dollars"] = prev_ot
        for flag in ("is_salaried", "multi_rate"):
            if prev.get(flag) and not rec.get(flag):
                merged[flag] = prev[flag]
        prev_wage = prev.get("wage_rate_dollars")
        if prev_wage is not None and abs(float(prev_wage) - float(wage)) > 0.005:
            old_f = float(prev_wage)
            new_f = float(wage)
            # A scraped rate outside the band, or more than a doubling/halving of
            # the known one, is a page artifact rather than a raise — the 1.25
            # that arrived for Browning, Garcia and Krause on 2026-09-15 was one
            # number read off three different pages, and only Krause was
            # salaried. A missing rate is recoverable from earnings; a wrong one
            # is silently wrong pay, so refuse and keep the old value.
            implausible = not (_RATE_FLOOR_DOLLARS <= new_f <= _RATE_CEILING_DOLLARS)
            if old_f > 0 and (implausible or new_f < 0.5 * old_f or new_f > 2.0 * old_f):
                print(
                    f"[pay_info] BREADCRUMB refused_rate_drop name={key} "
                    f"old={old_f} new={new_f}"
                )
                continue
            changes.append(
                {
                    "employee_name": key,
                    "old": old_f,
                    "new": new_f,
                }
            )
        fills.append(merged)
    return fills, changes


def names_with_nonnull_rates_bq() -> set[str]:
    from core.datastore import fq, get_client  # noqa: PLC0415

    client = get_client()
    if client is None:
        return set()
    sql = f"""
      SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name), ''), employee_id) AS n
      FROM {fq("adp_wage_rates")}
      WHERE wage_rate_dollars IS NOT NULL
    """
    return {row.n for row in client.query(sql).result() if row.n}


def puncher_names_from_session_files(
    *,
    timecard_xlsx: Optional[pathlib.Path],
    employee_aliases: Optional[dict] = None,
) -> list[str]:
    """Canonical names on tonight's timecard (if present).

    A hire not yet in the alias table comes out of the timecard raw
    (``Huang Wing``); alias onboarding runs later in the same night and keys
    them ``Huang, Wing``. ``derive_canonical`` applies the same rule here, so a
    first-night rate is not written under a key no shift ever joins on.
    """
    from skills.adp_run_automation import shift_backend as sb  # noqa: PLC0415
    from skills.adp_run_automation.employee_aliases import derive_canonical  # noqa: PLC0415

    if not timecard_xlsx or not timecard_xlsx.exists():
        return []
    punches = sb.parse_xlsx(timecard_xlsx, employee_aliases=employee_aliases)
    return sorted({
        derive_canonical(p["employee_name"]) for p in punches if p.get("employee_name")
    })


def gap_names_from_session_files(
    *,
    timecard_xlsx: Optional[pathlib.Path],
    earnings_xlsx: Optional[pathlib.Path],
    employee_aliases: Optional[dict] = None,
    excluded_employees: Optional[list[str]] = None,
) -> list[str]:
    """Timecard punchers minus tonight's Earnings Regular rates (and BQ if available)."""
    from skills.adp_run_automation import compensation_backend as cb  # noqa: PLC0415

    have: set[str] = set()
    try:
        have |= names_with_nonnull_rates_bq()
    except Exception as exc:  # noqa: BLE001
        print(f"[pay_info] BQ rate lookup skipped: {type(exc).__name__}: {exc}")

    if earnings_xlsx and earnings_xlsx.exists():
        earnings = cb.parse_xlsx(earnings_xlsx, employee_aliases=employee_aliases)
        for r in cb.infer_wage_rates(earnings, excluded_employees=excluded_employees):
            if r.get("wage_rate_dollars"):
                have.add(r["employee_name"])

    names = set(
        puncher_names_from_session_files(
            timecard_xlsx=timecard_xlsx, employee_aliases=employee_aliases
        )
    )
    return sorted(names - have)


def write_pay_info_rates_bq(rates: list[dict], *, dry_run: bool = False) -> int:
    """MERGE pay_info hourly rates (updates raises; preserves existing OT)."""
    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")
    from agents.bhaga.scripts.backfill_bigquery import (  # noqa: PLC0415
        load_store_profile,
        map_adp_wage_rate,
    )
    from core.datastore import ensure_schema, load_rows  # noqa: PLC0415

    if not rates:
        return 0
    ensure_schema()
    existing: dict[str, dict] = {}
    try:
        existing = existing_rates_bq()
    except Exception as exc:  # noqa: BLE001
        print(f"[pay_info] existing rate lookup skipped: {type(exc).__name__}: {exc}")
    fill, changes = prepare_pay_info_writes(rates, existing)
    for ch in changes:
        print(
            f"[pay_info] BREADCRUMB wage_rate_change name={ch['employee_name']} "
            f"old={ch['old']} new={ch['new']}"
        )
    if not fill:
        return 0
    from skills.adp_run_automation.wage_rate_history import record_pay_info_changes  # noqa: PLC0415

    profile = load_store_profile("palmetto")
    bq_rows = [map_adp_wage_rate(r, profile) for r in fill]
    if dry_run:
        print(f"[pay_info] DRY: would MERGE {len(bq_rows)} rows ({len(changes)} rate change(s))")
        for r in fill:
            print(f"  {r['employee_name']}: ${r['wage_rate_dollars']}")
        record_pay_info_changes(fill, dry_run=True)
        return 0
    n = load_rows(
        "adp_wage_rates",
        bq_rows,
        merge_keys=["employee_id"],
        column_bq_types={"scraped_at_utc": "TIMESTAMP"},
    )
    print(
        f"[pay_info] adp_wage_rates MERGE {n} rows "
        f"(rate_source=pay_info, changes={len(changes)})"
    )
    h = record_pay_info_changes(fill)
    print(f"[pay_info] adp_wage_rate_history MERGE {h} row(s)")
    return n


def report_pay_info_issues(
    *,
    date: str,
    scrape_errors: Optional[dict[str, str]] = None,
    remaining_gaps: Optional[list[str]] = None,
    flow_error: Optional[str] = None,
    attempted: int = 0,
    scraped_ok: int = 0,
) -> None:
    """Breadcrumb always; Slack only when someone actually ends up without a rate.

    There are two independent ways to get a wage rate — the People profile and
    the earnings report — and the whole point of having two is that either one
    can fail without anyone being worse off. Alerting on ``scrape_errors``
    reported the *mechanism* failing, so BHAGA DMed a "Failed scrapes" alert
    every night from August onward for two employees who had valid rates the
    entire time via ``rate_source = earnings``. An alert that is wrong every
    night trains you to ignore it, which is how the real 2026-09-07 failure sat
    unread for six days.

    ``remaining_gaps`` (from ``gap_names_from_bq``) is the outcome: punchers who
    have no rate from *any* source. That, and a flow error that prevented the
    check from running at all, are worth waking someone for. A scrape failure on
    its own is a breadcrumb.
    """
    scrape_errors = scrape_errors or {}
    remaining_gaps = remaining_gaps or []
    if not scrape_errors and not remaining_gaps and not flow_error:
        return
    print(
        f"[pay_info] BREADCRUMB wage_rate_flow_issue date={date} "
        f"attempted={attempted} ok={scraped_ok} "
        f"scrape_fail={len(scrape_errors)} gaps={remaining_gaps} "
        f"flow={flow_error or ''}"
    )
    if not remaining_gaps and not flow_error:
        print(
            f"[pay_info] {len(scrape_errors)} scrape failure(s) but every puncher "
            f"has a rate — breadcrumb only, no Slack alert: "
            f"{sorted(scrape_errors)}"
        )
        return
    try:
        from agents.bhaga.notify import wage_rate_flow_alert  # noqa: PLC0415

        wage_rate_flow_alert(
            date=date,
            scrape_errors=scrape_errors,
            remaining_gaps=remaining_gaps,
            flow_error=flow_error,
            attempted=attempted,
            scraped_ok=scraped_ok,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[pay_info] Slack wage-rate warning failed: {type(exc).__name__}: {exc}")


BLIND_STREAK_NIGHTS = 3
_BLIND_STATE_KEY = "pay_info_blind_streak"


def outcome_rows(payload: dict) -> list[dict]:
    """One ``adp_pay_info_outcomes`` row per attempted name in a PayInfoRates payload."""
    scraped_at = payload.get("scraped_at_utc")
    if not scraped_at:
        return []
    ok_names = {r.get("employee_name") for r in payload.get("rates") or []}
    errors = payload.get("errors") or {}
    rows = []
    for name in payload.get("attempted") or []:
        if name in ok_names:
            rows.append({"employee_id": name, "scraped_at_utc": scraped_at,
                         "ok": True, "error": None})
        elif name in errors:
            rows.append({"employee_id": name, "scraped_at_utc": scraped_at,
                         "ok": False, "error": str(errors[name])[:300]})
    return rows


def directory_status_rows(payload: dict) -> list[dict]:
    """One ``adp_directory_status`` row per Directory entry, stamped with the scrape time."""
    store = payload.get("store") or "palmetto"
    ts = payload.get("scraped_at_utc")
    return [
        {
            "store": store,
            "employee_name": " ".join(d["name"].split()),
            "employment_status": (d.get("status") or "").strip() or None,
            "scraped_at_utc": ts,
        }
        for d in payload.get("directory") or []
        if ts and (d.get("name") or "").strip()
    ]


def write_directory_status_bq(payload: dict) -> int:
    """Append tonight's Directory snapshot (re-loading the same JSON is a no-op)."""
    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")
    from core.datastore import ensure_schema, load_rows  # noqa: PLC0415

    rows = directory_status_rows(payload)
    if not rows:
        print("[pay_info] BREADCRUMB directory_status_empty — roster keeps the previous snapshot")
        return 0
    ensure_schema()
    return load_rows(
        "adp_directory_status",
        rows,
        merge_keys=["store", "employee_name", "scraped_at_utc"],
        column_bq_types={"scraped_at_utc": "TIMESTAMP", "employment_status": "STRING"},
    )


def record_pay_info_outcomes(payload: dict) -> int:
    """MERGE tonight's per-name outcomes (re-loading the same JSON is a no-op)."""
    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")
    from core.datastore import ensure_schema, load_rows  # noqa: PLC0415

    rows = outcome_rows(payload)
    if not rows:
        return 0
    ensure_schema()
    return load_rows(
        "adp_pay_info_outcomes",
        rows,
        merge_keys=["employee_id", "scraped_at_utc"],
        column_bq_types={"scraped_at_utc": "TIMESTAMP", "error": "STRING"},
    )


def blind_streaks(rows: list[dict]) -> dict[str, int]:
    """Consecutive most-recent attempted nights with no successful scrape, per name.

    ``rows``: ``{"employee_id", "night", "ok"}``, one per name × night with
    ``ok`` true if any attempt that night succeeded. A night with no attempt
    neither extends nor breaks a streak. Names currently OK are omitted.
    """
    by_name: dict[str, list[tuple]] = {}
    for r in rows:
        by_name.setdefault(r["employee_id"], []).append((r["night"], bool(r["ok"])))
    out: dict[str, int] = {}
    for name, nights in by_name.items():
        streak = 0
        for _, ok in sorted(nights, reverse=True):
            if ok:
                break
            streak += 1
        if streak:
            out[name] = streak
    return out


def pay_info_streak_rows_bq(*, nights: int = 14) -> list[dict]:
    """Per name × CT night: did any pay_info attempt succeed?"""
    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")
    from google.cloud import bigquery  # noqa: PLC0415

    from core.datastore import fq, get_client  # noqa: PLC0415

    client = get_client()
    if client is None:
        raise RuntimeError("BigQuery client unavailable — cannot read pay_info outcomes")
    sql = f"""
      SELECT employee_id, DATE(scraped_at_utc, 'America/Chicago') AS night,
             LOGICAL_OR(ok) AS ok
      FROM {fq("adp_pay_info_outcomes")}
      WHERE scraped_at_utc >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @n DAY)
      GROUP BY 1, 2
    """
    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("n", "INT64", nights)]
        ),
    )
    return [dict(row.items()) for row in job.result()]


def report_blind_streaks(
    *, date: str, streaks: dict[str, int], errors: Optional[dict[str, str]] = None,
) -> list[str]:
    """Breadcrumb every night; Slack once per streak when a name reaches 3 nights.

    ``report_pay_info_issues`` deliberately stays quiet while earnings still
    supplies a rate. That left 2026-09-29/30 — every scrape failing, 0 of 16 —
    without a single alert, while any raise stayed invisible until the next
    paycheck. This alerts on that sustained blindness, once: the remembered
    set is tonight's blind names, so a name alerts again only after it
    recovers and goes blind anew.
    """
    blind = sorted(n for n, s in streaks.items() if s >= BLIND_STREAK_NIGHTS)
    print(
        f"[pay_info] BREADCRUMB pay_info_blind_streak date={date} "
        f"blind={blind} streaks={dict(sorted(streaks.items()))}"
    )
    already: list[str] = []
    try:
        from skills.bhaga_config.state_adapter import get_notify_state  # noqa: PLC0415

        already = get_notify_state(_BLIND_STATE_KEY)
    except Exception:  # noqa: BLE001
        already = []
    from agents.bhaga.notify import (  # noqa: PLC0415
        partition_anomalies,
        pay_info_blind_alert,
    )

    new, _ = partition_anomalies(blind, already)
    try:
        from skills.bhaga_config.state_adapter import set_notify_state  # noqa: PLC0415

        set_notify_state(_BLIND_STATE_KEY, blind)
    except Exception as exc:  # noqa: BLE001
        print(f"[pay_info] blind-streak state write failed: {type(exc).__name__}: {exc}")
    if new:
        pay_info_blind_alert(
            date=date, names=new, streaks=streaks, errors=errors or {},
        )
    return new


def assert_no_missing_puncher_rates(*, days: int = 60) -> list[str]:
    """Return remaining gaps; print greppable breadcrumb when non-empty."""
    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")
    gaps = gap_names_from_bq(days=days)
    if gaps:
        print(
            f"[pay_info] BREADCRUMB wage_rate_gap days={days} "
            f"missing={len(gaps)} names={gaps}"
        )
    else:
        print(f"[pay_info] OK — no punchers missing wage rates in last {days}d")
    return gaps


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", default="palmetto")
    ap.add_argument("--names", nargs="*", default=[], help="Canonical names to scrape")
    ap.add_argument("--from-bq-gaps", action="store_true", help="Scrape only punchers missing a rate")
    ap.add_argument(
        "--from-bq-punchers",
        action="store_true",
        help="Scrape all punchers in --days (nightly default in the ADP bundle)",
    )
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--write-bq", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true", default=True)
    ap.add_argument("--assert-gaps", action="store_true", help="Only print remaining gaps")
    args = ap.parse_args(argv)

    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")

    if args.assert_gaps:
        gaps = assert_no_missing_puncher_rates(days=args.days)
        return 1 if gaps else 0

    names = list(args.names)
    if args.from_bq_punchers:
        names = puncher_names_from_bq(days=args.days)
        print(f"[pay_info] BQ punchers ({args.days}d): {names}")
    elif args.from_bq_gaps:
        names = gap_names_from_bq(days=args.days)
        print(f"[pay_info] BQ gaps ({args.days}d): {names}")
    if not names:
        print("[pay_info] no names to scrape")
        return 0

    from skills.adp_run_automation.runner import adp_session  # noqa: PLC0415
    from skills.store_profile import load_aliases  # noqa: PLC0415

    aliases = load_aliases(args.store)
    with adp_session(store=args.store, headed=args.headed, slow_mo_ms=50) as (_ctx, page):
        dashboard_url = page.url
        rates, errors = scrape_pay_info_rates(
            page, names, dashboard_url=dashboard_url, aliases=aliases,
        )
        write_pay_info_json(rates, store=args.store, errors=errors, attempted=names)
        if args.write_bq or args.dry_run:
            write_pay_info_rates_bq(rates, dry_run=args.dry_run)
        remaining: list[str] = []
        if args.write_bq and not args.dry_run:
            remaining = assert_no_missing_puncher_rates(days=args.days)
        if errors or remaining:
            report_pay_info_issues(
                date=datetime.date.today().isoformat(),
                scrape_errors=errors,
                remaining_gaps=remaining,
                attempted=len(names),
                scraped_ok=len(rates),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
