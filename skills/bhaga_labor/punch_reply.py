"""Read a staff reply in the missing-punch thread into punch decisions (Issue #397).

Staff answer the daily ClickUp post in plain words — "out 3:20", "in 2:05",
"2:15-8:45", "yes", "didn't work that day". This turns one reply plus the
open items it can refer to (that person's rows in that post) into one
``Resolution`` per item it clearly settles. Anything unclear resolves nothing:
the item stays open and is listed again in the next daily post, which is the
whole error path — BHAGA never replies in the thread.

Pure module — no IO, no clock. Rules that are easy to get backwards:

  * A reply only fills the blank side. "9-3" on a missing clock-out sets the
    out to 3:00 and leaves the punched clock-in alone.
  * "Yes" means "use my scheduled time"; it only resolves an item that has a
    suggestion to accept.
  * "Didn't work" only dismisses a no-punch day. On a day with a punch it is a
    contradiction and resolves nothing.
  * A time without am/pm is read as whichever of the two is nearer the
    scheduled time it answers (the shift's start for a clock-in, its end for a
    clock-out).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Optional

from skills.bhaga_labor.punch_gaps import (
    MISSING_IN, MISSING_OUT, MISSING_OUT_AFTER_BREAK, NO_ENTRY, merge_ranges, to_minutes,
)

MAX_SHIFT_MINUTES = 16 * 60
FILLS_OUT = (MISSING_OUT, MISSING_OUT_AFTER_BREAK)


@dataclass(frozen=True)
class ReplyItem:
    date: datetime.date
    kind: str
    entries: tuple[tuple[Optional[str], Optional[str]], ...]
    open_entry_index: Optional[int]
    scheduled: tuple[tuple[str, str], ...]
    suggested_in: Optional[str] = None
    suggested_out: Optional[str] = None

    @property
    def punched_in(self) -> Optional[str]:
        """The clock-in of the open entry (missing clock-out)."""
        i = self.open_entry_index
        return self.entries[i][0] if i is not None and i < len(self.entries) else None

    @property
    def punched_out(self) -> Optional[str]:
        """The clock-out of the entry with no clock-in (missing clock-in)."""
        i = self.open_entry_index
        return self.entries[i][1] if i is not None and i < len(self.entries) else None


@dataclass(frozen=True)
class Resolution:
    date: datetime.date
    action: str  # accept | edit | reject
    in_time: Optional[str]
    out_time: Optional[str]


_MENTION = re.compile(r"\[@[^\]]*\]\(#user_mention#\d+\)|@\w+")
_DATE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?:/\d{2,4})?(?!\d)")
_WEEKDAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "weds": 2,
    "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}
_WEEKDAY = re.compile(r"\b(" + "|".join(sorted(_WEEKDAYS, key=len, reverse=True)) + r")\b")
_TIME = re.compile(
    r"(?<![\d:])(\d{1,2})(?::([0-5]\d))?(?![\d:])\s*(?:(am|pm|a\.m\.|p\.m\.|a|p)(?![a-z]))?"
)
_RANGE_SEP = re.compile(r"^\s*(?:-|–|—|to|till|until|thru|through)\s*$")
_BARE_BEFORE = re.compile(r"(?:\bat|@|\btill|\buntil|\bto|\bfrom|\bin|\bout|\bleft|-|–|—)\s*$")
_DURATION_AFTER = re.compile(r"^\s*(?:days?|hrs?|hours?|mins?|minutes?|weeks?)\b")
_IN_WORD = re.compile(
    r"\b(?:in|clocked in|clock in|clock-in|started|start|came in|got in|arrived)\s*(?:at|around|@)?\s*$"
)
_OUT_WORD = re.compile(
    r"\b(?:out|clocked out|clock out|clock-out|left|leave|ended|end|off|done|finished)"
    r"\s*(?:at|around|@)?\s*$"
)
_REJECT = re.compile(
    r"\b(?:didn'?t|did not|wasn'?t|was not|never)\s+(?:work|come|go|clock|there|in)"
    r"|\b(?:day off|was off|off that day|called out|call out|called in sick|sick|no shift"
    r"|not scheduled|not working)\b|^\s*off\s*[.!]*\s*$"
)
_CONFIRM = re.compile(
    r"\b(?:yes|yep|yeah|yup|ya|ok|okay|correct|right|confirm(?:ed)?|sounds good|good|fine"
    r"|worked|accurate|agree[d]?)\b|👍|✅|✔"
)


@dataclass(frozen=True)
class _Tok:
    start: int
    end: int
    hour: int
    minute: int
    ampm: Optional[str]  # 'a' | 'p' | None
    side: Optional[str]  # 'in' | 'out' | None (keyword just before it)


def _clean(text: str) -> str:
    text = _MENTION.sub(" ", text or "").replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


def _tokens(text: str) -> list[_Tok]:
    out: list[_Tok] = []
    for m in _TIME.finditer(text):
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        ap = (m.group(3) or "")[:1] or None
        before = text[:m.start()]
        after = text[m.end():]
        bare = not m.group(2) and not ap
        if bare and (_DURATION_AFTER.match(after) or not (
                _BARE_BEFORE.search(before)
                or re.match(r"^\s*(?:-|–|—|to|till|until)\s*\d", after))):
            continue
        if ap and not 1 <= h <= 12:
            continue
        if not ap and not (0 <= h <= 23):
            continue
        side = "in" if _IN_WORD.search(before[-20:]) else "out" if _OUT_WORD.search(before[-20:]) else None
        out.append(_Tok(m.start(), m.end(), h, mi, ap, side))
    return out


def _resolve_clock(tok: _Tok, ref: Optional[int]) -> int:
    """Minutes after midnight; am/pm from the token, else nearest to ``ref``."""
    if tok.ampm:
        return (tok.hour % 12 + (12 if tok.ampm == "p" else 0)) * 60 + tok.minute
    if tok.hour > 12 or tok.hour == 0:
        return tok.hour * 60 + tok.minute
    am = (tok.hour % 12) * 60 + tok.minute
    if ref is None:
        return am
    return min((am, am + 720), key=lambda c: abs(c - ref))


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _anchors(item: ReplyItem) -> tuple[Optional[int], Optional[int]]:
    """(reference for a clock-in, reference for a clock-out) in minutes."""
    spans = merge_ranges(list(item.scheduled))
    in_ref = spans[0][0] if spans else None
    out_ref = spans[-1][1] if spans else None
    if item.kind in FILLS_OUT and item.punched_in:
        in_ref = to_minutes(item.punched_in)
        later = [b for a, b in spans if b > in_ref]
        out_ref = later[0] if later else in_ref + 6 * 60
    if item.kind == MISSING_IN and item.punched_out:
        out_ref = to_minutes(item.punched_out)
        earlier = [a for a, b in spans if a < out_ref]
        in_ref = earlier[-1] if earlier else out_ref - 6 * 60
    return in_ref, out_ref


def _ranges(toks: list[_Tok], text: str) -> list[tuple[_Tok, _Tok]]:
    pairs = []
    i = 0
    while i + 1 < len(toks):
        if _RANGE_SEP.match(text[toks[i].end:toks[i + 1].start]):
            pairs.append((toks[i], toks[i + 1]))
            i += 2
        else:
            i += 1
    return pairs


def _targets(text: str, items: list[ReplyItem]) -> list[ReplyItem]:
    dates = {(int(m), int(d)) for m, d in _DATE.findall(text)}
    weekdays = {_WEEKDAYS[w] for w in _WEEKDAY.findall(_DATE.sub(" ", text))}
    if not dates and not weekdays:
        return sorted(items, key=lambda it: it.date)
    return sorted(
        (it for it in items
         if (it.date.month, it.date.day) in dates or it.date.weekday() in weekdays),
        key=lambda it: it.date,
    )


def _valid(item: ReplyItem, in_min: Optional[int], out_min: Optional[int]) -> Optional[Resolution]:
    """Fill only the blank side; refuse an out not after the in or a >16 h span."""
    if item.kind in FILLS_OUT:
        in_min = to_minutes(item.punched_in) if item.punched_in else None
    elif item.kind == MISSING_IN:
        out_min = to_minutes(item.punched_out) if item.punched_out else None
    if in_min is None or out_min is None:
        return None
    if not 0 < out_min - in_min <= MAX_SHIFT_MINUTES or out_min > 23 * 60 + 59:
        return None
    return Resolution(
        item.date, "edit",
        None if item.kind in FILLS_OUT else _fmt(in_min),
        _fmt(out_min),
    )


def _from_times(item: ReplyItem, toks: list[_Tok], pair: Optional[tuple[_Tok, _Tok]],
                only_target: bool) -> Optional[Resolution]:
    in_ref, out_ref = _anchors(item)
    if pair:
        a = _resolve_clock(pair[0], in_ref)
        b = _resolve_clock(pair[1], out_ref)
        if b <= a and not pair[1].ampm and b + 720 <= 23 * 60 + 59:
            b += 720
        return _valid(item, a, b)
    ins = [t for t in toks if t.side == "in"]
    outs = [t for t in toks if t.side == "out"]
    bare = [t for t in toks if t.side is None]
    if item.kind == NO_ENTRY:
        if len(ins) == 1 and len(outs) == 1:
            return _valid(item, _resolve_clock(ins[0], in_ref), _resolve_clock(outs[0], out_ref))
        return None
    want, other = (outs, ins) if item.kind in FILLS_OUT else (ins, outs)
    if len(want) == 1:
        tok = want[0]
    elif not want and not other and len(bare) == 1 and only_target:
        tok = bare[0]
    else:
        return None
    if item.kind in FILLS_OUT:
        return _valid(item, None, _resolve_clock(tok, out_ref))
    return _valid(item, _resolve_clock(tok, in_ref), None)


def _from_words(item: ReplyItem, text: str) -> Optional[Resolution]:
    if _REJECT.search(text):
        return Resolution(item.date, "reject", None, None) if item.kind == NO_ENTRY else None
    if not _CONFIRM.search(text):
        return None
    if item.kind in FILLS_OUT and item.suggested_out:
        return Resolution(item.date, "accept", None, item.suggested_out)
    if item.kind == MISSING_IN and item.suggested_in and item.punched_out:
        return Resolution(item.date, "accept", item.suggested_in, item.punched_out)
    if item.kind == NO_ENTRY and item.suggested_in and item.suggested_out:
        return Resolution(item.date, "accept", item.suggested_in, item.suggested_out)
    return None


def parse_reply(text: str, items: list[ReplyItem]) -> list[Resolution]:
    """Resolutions for the items this reply clearly settles (possibly none)."""
    clean = _clean(text)
    targets = _targets(clean, items)
    if not targets:
        return []
    timed = _DATE.sub(lambda m: " " * len(m.group(0)), clean)
    toks = _tokens(timed)
    if not toks:
        return [r for r in (_from_words(it, clean) for it in targets) if r]
    pairs = _ranges(toks, timed)
    if len(targets) == 1:
        if len(pairs) > 1:
            return []
        r = _from_times(targets[0], toks, pairs[0] if pairs else None, only_target=True)
        return [r] if r else []
    # Several open days: times only count when there is exactly one range per day, in date order.
    if len(pairs) == len(targets) and len(pairs) * 2 == len(toks):
        out = [_from_times(it, list(p), p, only_target=False) for it, p in zip(targets, pairs)]
        return [r for r in out if r]
    return []
