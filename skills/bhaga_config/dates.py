"""Date helpers for the BHAGA Model config tab.

Single source of truth for parsing and emitting date-shaped values
in `bhaga_model > config`. See `seamless_bhaga_refresh` PR description
for why this exists (root cause: Google Sheets auto-coerces ISO date
strings into serial integers under valueInputOption=USER_ENTERED).

Two complementary primitives:

- ``coerce_iso_date(value)``  — READ side. Accepts ISO, apostrophe-
  prefixed ISO, or a Sheets serial integer/float, and returns the
  canonical ``"YYYY-MM-DD"`` string. Returns ``None`` for empty or
  unparseable input.

- ``_iso_date_for_sheet_cell(value)`` — WRITE side. Wraps a date or
  ISO-string in a leading apostrophe so the Sheets API
  (valueInputOption=USER_ENTERED) keeps it as a text literal instead
  of coercing it to a date-serial. Idempotent — never double-prefixes.
"""

from __future__ import annotations

import datetime

# Google Sheets' date serial epoch is 1899-12-30 (a Lotus 1-2-3 quirk
# that Excel/Sheets both inherit). Day 1 = 1899-12-31, day 2 = 1900-01-01.
SHEETS_DATE_EPOCH = datetime.date(1899, 12, 30)

# Sanity range for serial-to-date recovery. Hand-picked so that out-of-
# range "looks like a serial" junk (e.g. "1", "100000") still falls
# through to the unparseable branch.
#   40000 ≈ 1909-07-09  (just below floor → reject)
#   40001 ≈ 1909-07-10  (just inside → accept)
#   80000 ≈ 2119-01-25  (just above ceiling → reject)
_SERIAL_MIN = 40_001
_SERIAL_MAX = 79_999


def coerce_iso_date(value) -> str | None:
    """Normalize a config-cell date value to canonical ``YYYY-MM-DD``.

    Accepts:
      - ``"2026-05-20"``               → ``"2026-05-20"``  (happy path)
      - ``"'2026-05-20"``              → ``"2026-05-20"``  (Layer A's own output)
      - ``"46162"`` / ``46162``        → ``"2026-05-20"``  (Sheets serial drift)
      - ``"46162.0"``                  → ``"2026-05-20"``  (float serial)
      - ``" 2026-05-20 "``             → ``"2026-05-20"``  (whitespace tolerance)
      - ``""`` / ``None``              → ``None``          (empty signals fresh install)
      - anything else                  → ``None``          (caller decides what to do)
    """
    if value is None:
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n_float = float(value)
        n_int = int(n_float)
        if n_int == n_float and _SERIAL_MIN <= n_int <= _SERIAL_MAX:
            return (SHEETS_DATE_EPOCH + datetime.timedelta(days=n_int)).isoformat()
        return None
    if not isinstance(value, str):
        return None
    s = value.strip().lstrip("'").strip()
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s).isoformat()
    except ValueError:
        pass
    try:
        n_float = float(s)
    except ValueError:
        return None
    n_int = int(n_float)
    if n_int != n_float:
        return None
    if _SERIAL_MIN <= n_int <= _SERIAL_MAX:
        return (SHEETS_DATE_EPOCH + datetime.timedelta(days=n_int)).isoformat()
    return None


def _iso_date_for_sheet_cell(value) -> str:
    """Wrap a date/ISO-string in a leading apostrophe for Sheets text-literal.

    Idempotent and normalizing: routes the input through
    ``coerce_iso_date`` first so a Sheets-serial read-back (e.g.
    ``"46162"`` left over from a pre-fix corrupt cell) gets converted
    back to canonical ISO BEFORE the apostrophe wrap — otherwise we'd
    just persist the same drift in text form, which defeats Layer A's
    purpose. If ``coerce_iso_date`` can't normalize, fall back to a
    plain stripped+apostrophe-prefix so the cell still becomes text
    instead of getting auto-coerced by Sheets.

    Contract:
      - ``None``                       → ``""``        (empty cell)
      - ``datetime.date(2026,5,20)``   → ``"'2026-05-20"``
      - ``"2026-05-20"``               → ``"'2026-05-20"``
      - ``"'2026-05-20"``              → ``"'2026-05-20"``  (no double-prefix)
      - ``"  2026-05-20  "``           → ``"'2026-05-20"``  (whitespace stripped)
      - ``"46162"`` / ``46162``        → ``"'2026-05-20"``  (serial drift recovered)
      - any other string               → ``"'<stripped>"``  (best-effort —
        caller fed something we couldn't normalize; we still prepend
        so Sheets won't coerce it)
    """
    if value is None:
        return ""
    coerced = coerce_iso_date(value)
    if coerced is not None:
        return f"'{coerced}"
    s = str(value).strip()
    if not s:
        return ""
    if s.startswith("'"):
        return s
    return f"'{s}"
