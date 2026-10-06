"""Approve one pending ADP unavailability request — Issue #337.

Team Schedule › Pending requests › Unavailability Requests lists one card per
request, each with its own APPROVE / REJECT (card shape: schedule_backend
§ Unavailability). The only commit button pressed is the matching card's
APPROVE (plus a confirmation dialog's Approve if ADP shows one). Never REJECT.

Every call is an operator click behind ``FEATURES.adpUnavailabilityApprove``.
A card that cannot be matched to exactly one request is never clicked, and an
unconfirmed click is reported, not retried.
"""

from __future__ import annotations

import datetime as dt
import re

from skills.adp_run_automation import schedule_backend as sb

# Each APPROVE button's card: the largest ancestor that still holds only that
# one APPROVE button. Light DOM inside the requests pane.
CARDS_JS = r"""
() => {
  const isApprove = b => /^\s*approve\s*$/i.test(b.innerText || b.getAttribute('aria-label') || '');
  const pane = [...document.querySelectorAll('sdf-focus-pane')].find(p => {
    const r = p.getBoundingClientRect(); return r.width > 0 && r.height > 0;
  });
  if (!pane) return [];
  const buttons = [...pane.querySelectorAll('button, sdf-button')].filter(isApprove);
  return buttons.map((b, i) => {
    let card = b;
    while (card.parentElement && card.parentElement !== pane &&
           [...card.parentElement.querySelectorAll('button, sdf-button')].filter(isApprove).length === 1) {
      card = card.parentElement;
    }
    b.setAttribute('data-jarvis-approve', String(i));
    return { index: i, text: card.innerText || '' };
  });
}
"""

KEY_FIELDS = ("raw_employee_name", "first_date", "from_time", "to_time")


class ApproveError(RuntimeError):
    """The request could not be approved; nothing was clicked unless stated."""


class UnconfirmedApprove(ApproveError):
    """APPROVE was clicked but the card did not go away — check ADP before retrying."""


def _norm(name: str) -> str:
    return " ".join((name or "").split()).lower()


def card_key(text: str, *, scraped_on: dt.date) -> tuple | None:
    """One card's text → (name, first_date, from_time, to_time), or None if unparseable."""
    body = text if re.search(r"Unavailability\s+(update|request)", text, re.I) else f"Unavailability update\n{text}"
    rows = sb.parse_unavailability_requests(body, scraped_on=scraped_on)
    if len(rows) != 1:
        return None
    r = rows[0]
    return (_norm(r["raw_employee_name"]), r["first_date"], r["from_time"], r["to_time"])


def match_card(cards: list[dict], target: dict, *, scraped_on: dt.date) -> int:
    """Index of the one card matching ``target`` (a stored pending row).

    Raises ApproveError when zero or several cards match — never guesses.
    """
    want = (_norm(target["raw_employee_name"]), target["first_date"], target.get("from_time"), target.get("to_time"))
    hits = [c["index"] for c in cards if card_key(c["text"], scraped_on=scraped_on) == want]
    if len(hits) == 1:
        return hits[0]
    listed = "; ".join(" ".join(c["text"].split())[:80] for c in cards) or "no cards"
    if not hits:
        raise ApproveError(f"no pending ADP card matches {want} (cards: {listed})")
    raise ApproveError(f"{len(hits)} ADP cards match {want} — not approving an ambiguous request")


def _approve_count(frame) -> int:
    return len(frame.evaluate(CARDS_JS))


def approve(page, frame, target: dict, *, dry_run: bool = False) -> None:
    """Approve the pending card for ``target``; ``dry_run`` matches it and stops."""
    from skills.adp_run_automation import runner as r

    _pane, _list_text, unavail_text = r._open_unavailability_requests(page, frame)
    if not unavail_text:
        raise ApproveError("ADP lists no unavailability requests")
    page.wait_for_timeout(1000)
    cards = frame.evaluate(CARDS_JS)
    idx = match_card(cards, target, scraped_on=dt.date.today())
    print(f"[unavail_approve] matched card {idx + 1} of {len(cards)} dry_run={dry_run}")
    if dry_run:
        return
    before = len(cards)
    frame.locator(f"[data-jarvis-approve='{idx}']").first.click(timeout=8000)
    page.wait_for_timeout(2000)
    if _approve_count(frame) == before:
        dialog = frame.locator("[role=dialog], sdf-modal, sdf-alert").filter(
            has=frame.get_by_role("button", name=re.compile(r"^\s*(approve|yes|confirm)\s*$", re.I))
        )
        if dialog.count():
            dialog.first.get_by_role("button", name=re.compile(r"^\s*(approve|yes|confirm)\s*$", re.I)).first.click(
                timeout=8000
            )
            page.wait_for_timeout(2500)
    for _ in range(10):
        if _approve_count(frame) < before:
            return
        page.wait_for_timeout(1000)
    raise UnconfirmedApprove(
        f"ADP still shows the request for {target['raw_employee_name']} on {target['first_date']} — check ADP before retrying"
    )
