"""Post a ClickUp chat message through the web app so @mentions are real chips (Issue #435).

The REST API stores ``@everyone`` as plain text. Typing a mention into the composer
and picking ClickUp's own autocomplete entry is what makes it a real Everyone /
person mention, and the chips survive copy-paste into another channel.

``content`` is plain text with two kinds of mention:
  * ``@everyone``                       → the channel's Everyone chip
  * ``[@First Last](#user_mention#<id>)`` → that person's chip (picked by ClickUp user id)
A person who can't be picked is typed as plain ``First Last`` and reported in ``plain``.
``**text**`` is typed bold; a ``---`` line is dropped.

Session: a Playwright ``storage_state`` of the operator's ClickUp sign-in
(``cu_refresh`` lasts about a year). The file is rewritten after each send so
refreshed tokens persist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r"\[@([^\]]+)\]\(#user_mention#(\d+)\)|@everyone\b")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
COMPOSER = '[data-test="comment-bar__editor-input"]'
OPTION = "cu3-combo-box-item[role=option]"
APP = "https://app.clickup.com"


class SessionExpired(RuntimeError):
    """The stored ClickUp session no longer signs in."""


@dataclass
class SendResult:
    everyone: bool = False
    users: list[str] = field(default_factory=list)
    plain: list[str] = field(default_factory=list)


def segments(content: str) -> list[tuple[str, str, str]]:
    """Split ``content`` into ("text", s, "") / ("everyone", "", "") / ("user", name, id)."""
    out: list[tuple[str, str, str]] = []
    pos = 0
    for m in TOKEN_RE.finditer(content):
        if m.start() > pos:
            out.append(("text", content[pos:m.start()], ""))
        out.append(("user", m.group(1), m.group(2)) if m.group(1) else ("everyone", "", ""))
        pos = m.end()
    if pos < len(content):
        out.append(("text", content[pos:], ""))
    return out


def plain_text(content: str) -> str:
    """``content`` as the REST API would show it: ``@First Last`` for each person token."""
    return TOKEN_RE.sub(lambda m: f"@{m.group(1)}" if m.group(1) else m.group(0), content)


def _type_text(page, text: str) -> None:
    # insert_text, not type: a typed "#", "/" or ":" opens a ClickUp picker that eats the
    # keys after it. A "---" line becomes a divider that moves the caret, so it is dropped.
    # Inserted text skips ClickUp's markdown shortcuts, so **bold** is typed with Ctrl+B.
    lines = re.sub(r"\n*^---$\n*", "\n\n", text, flags=re.M).split("\n")
    for i, line in enumerate(lines):
        if i:
            page.keyboard.press("Shift+Enter")
        for j, part in enumerate(BOLD_RE.split(line)):
            if j % 2:
                page.keyboard.press("Control+B")
                page.keyboard.insert_text(part)
                page.keyboard.press("Control+B")
            elif part:
                page.keyboard.insert_text(part)


def _pick(page, query: str, option) -> bool:
    """Type ``@query`` and click ``option``; on no match erase the query and return False."""
    page.keyboard.type("@" + query, delay=60)
    try:
        option.first.wait_for(state="visible", timeout=8000)
        option.first.click()
    except Exception:  # noqa: BLE001 — no match: fall back to plain text
        for _ in range(len(query) + 1):
            page.keyboard.press("Backspace")
        page.keyboard.press("Escape")
        return False
    page.wait_for_timeout(300)
    # Mentioning someone outside the channel asks to add them; never add.
    dont_add = page.get_by_role("button", name="Don't Add")
    if dont_add.count():
        dont_add.first.click()
    return True


def type_message(page, content: str) -> SendResult:
    """Type ``content`` into the focused composer, turning mention tokens into chips."""
    res = SendResult()
    chip = False  # ClickUp puts a space after a picked chip
    for kind, value, user_id in segments(content):
        if kind == "text":
            _type_text(page, value[1:] if chip and value.startswith(" ") else value)
            chip = False
        elif kind == "everyone":
            res.everyone = chip = _pick(page, "every", page.locator(OPTION).filter(has_text="Everyone"))
            if not res.everyone:
                page.keyboard.insert_text("@everyone")
        elif _pick(page, value, page.locator(f"{OPTION}#item_{user_id}")):
            res.users.append(user_id)
            chip = True
        else:
            page.keyboard.insert_text(value)
            res.plain.append(value)
            chip = False
    return res


def send(
    channel_id: str,
    content: str,
    *,
    team_id: str,
    storage_state: str,
    headed: bool = False,
) -> SendResult:
    """Open the channel signed in from ``storage_state``, type ``content`` and send it."""
    from skills._browser_runtime.runtime import launch_persistent  # noqa: PLC0415 — heavy, browser image only

    with launch_persistent("clickup", headed=headed, storage_state=storage_state,
                           accept_downloads=False) as (ctx, page):
        page.goto(f"{APP}/{team_id}/chat/r/{channel_id}", wait_until="domcontentloaded")
        composer = page.locator(COMPOSER)
        try:
            composer.first.wait_for(state="visible", timeout=60_000)
        except Exception as exc:  # noqa: BLE001
            raise SessionExpired(f"no ClickUp composer at {page.url} — session expired?") from exc
        page.wait_for_timeout(2000)  # mention pickers attach after the composer shows
        box = composer.first.bounding_box()
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        # ClickUp restores an unsent draft per channel; start from an empty composer.
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        res = type_message(page, content)
        page.keyboard.press("Enter")
        page.wait_for_function(
            "sel => (document.querySelector(sel)?.innerText || '').trim() === ''", arg=COMPOSER, timeout=15_000,
        )
        page.wait_for_timeout(1500)
        ctx.storage_state(path=storage_state)
    return res
