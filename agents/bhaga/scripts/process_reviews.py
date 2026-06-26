#!/usr/bin/env python3
"""BHAGA Google review processor — fetch from ClickUp, allocate bonuses.

End-to-end flow:
    1. Read `last_processed_ts_ms` from BHAGA Review Raw sheet's config tab.
       (Source-of-truth for incremental fetches.)
    2. Fetch new messages from ClickUp channel #running-austin-palmetto
       via skills/clickup_chat (REST direct, PAT in Keychain).
    3. For each message that starts with "### Google Review":
         a. Parse structured fields (post time, rating, reviewer, comment).
         b. If unparseable -> log to `unparseable` tab.
         c. Else: determine shift_date + on-shift employees per these rules:
              - If post-time is within an active punch -> in-hours mode.
              - Else -> "last shift" mode (most recent punch before post).
            For named-shoutouts: scan the comment for first-names of any
            employee who worked ANY punch on shift_date (whole-day search).
         d. Apply exclusions (permanent + training_through). Excluded
            employees get $0 (no redistribution — review bonus is per-person,
            not a pool).
         e. Compute bonuses (updated 2026-05-17):
              - Shoutout reviews (named non-empty): ONLY the named
                people get $20 each. Shoutouts OVERRIDE exclusions —
                if a customer names a manager (e.g. Lindsay) or a
                trainee (e.g. Juan, Emely), they still earn the $20.
                Other shift members not named earn nothing.
              - No-shoutout 5★ reviews: every non-excluded shift
                member gets $10 base. Permanent and training
                exclusions DO apply here.
    4. Append parsed reviews to `reviews` tab (idempotent by review_id).
    5. Rebuild `review_bonus_period` tab on the Model sheet — per-employee
       per-payroll-cycle rollup that joins naturally with tip_alloc_period.
    6. Update config tab high-water marks.
    7. Send Slack summary (success or anomaly alert).

INCREMENTAL CONTRACT:
    The Review Raw sheet's config tab is the source of truth for what we've
    already processed. Never re-credits the same review_id twice. For an
    explicit backfill from a specific date, use --since YYYY-MM-DD.

CLI:
    python3 -m agents.bhaga.scripts.process_reviews --store palmetto
    python3 -m agents.bhaga.scripts.process_reviews --store palmetto --since 2026-05-11
    python3 -m agents.bhaga.scripts.process_reviews --store palmetto --dry-run --no-slack
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from agents.bhaga.notify import (  # noqa: E402
    failure_alert,
    info_ping,
    review_anomaly_alert,
    success_heartbeat,
)
from agents.bhaga.scripts.update_model_sheet import (  # noqa: E402
    add_sheet_if_missing,
    bold_header_row,
    clear_and_write_tab,
    format_currency_columns,
)
from core.config_loader import refresh_access_token, resolve_sheet_id  # noqa: E402
from core.datastore import _is_enabled as _bq_enabled, fq, load_rows, read_query  # noqa: E402
from agents.bhaga.scripts.backfill_bigquery import map_google_review  # noqa: E402
from skills.adp_run_automation.shift_backend import normalize_employee_name  # noqa: E402
from skills.bhaga_config.dates import (  # noqa: E402
    _iso_date_for_sheet_cell,
    coerce_iso_date,
)
from skills.tip_ledger_writer import read_raw_adp_punches  # noqa: E402
from skills.clickup_chat import fetch_messages  # noqa: E402

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
STORE_PROFILES = PROJECT_ROOT / "agents" / "bhaga" / "knowledge-base" / "store-profiles"
DOWNLOADS = PROJECT_ROOT / "extracted" / "downloads"
CT = ZoneInfo("America/Chicago")
SHEETS_API = "https://sheets.googleapis.com/v4"

# Source channel for the auto-posted reviews. Hardcoded because there's only
# ever one. If the channel id ever changes, update both places consistently.
REVIEW_CHANNEL_ID = "8cr6661-737"
REVIEW_CHANNEL_NAME = "running-austin-palmetto"
CLICKUP_TEAM_ID = "9017956545"

# Bonus dollar amounts (matches the May 11 announcement examples; the "$5"
# in the announcement header text is treated as a typo per user confirmation).
# Bonus constants. Defaults below; overridden at runtime from
# bhaga_model > config (review_base_bonus_dollars / review_named_bonus_dollars /
# review_bonus_started_date) so the operator can tune them in-sheet.
BASE_BONUS_DOLLARS = 10
NAMED_BONUS_DOLLARS = 20
# Reviews must be on or after this date to qualify (announcement effective date).
BONUS_START_DATE = datetime.date(2026, 5, 11)
# Pool model: fixed $20 per qualifying review, split equally among in-hours
# non-excluded part-time staff. Active for reviews posted on/after POOL_EFFECTIVE_DATE.
POOL_EFFECTIVE_DATE = datetime.date(2026, 6, 8)
POOL_DOLLARS = 20

# Sheet-config keys (config tab of BHAGA Review Raw).
# bhaga_model > config keys for review-bonus tuning. These three are the
# only config keys this script reads. There is NO config tab on
# bhaga_review_raw — the incremental high-water mark is derived from the
# `reviews` tab itself (max post_ts_ct), not stored anywhere.
MODEL_CFG_BONUS_START = "review_bonus_started_date"
MODEL_CFG_BASE_BONUS = "review_base_bonus_dollars"
MODEL_CFG_NAMED_BONUS = "review_named_bonus_dollars"
# Pool-model config keys (effective 2026-06-08).
MODEL_CFG_POOL_EFFECTIVE = "review_pool_effective_date"
MODEL_CFG_POOL_DOLLARS = "review_pool_dollars"

# Marker that identifies an automated review post in the channel.
REVIEW_HEADER = "### Google Review"

# Word -> star count for the Rating field (covers both casing variants seen).
RATING_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
}


# ── Profile + sheet helpers ──────────────────────────────────────────


def _load_profile(store: str) -> dict:
    return json.loads((STORE_PROFILES / f"{store}.json").read_text())


# Config keys whose values are dates. On read, route through
# coerce_iso_date so apostrophe-prefixed ISO strings AND Sheets date-
# serial drift both normalize to canonical "YYYY-MM-DD" before
# downstream code sees them. Keep this aligned with
# `agents.bhaga.scripts.update_model_sheet._DATE_CONFIG_KEYS` (the
# write side).
_DATE_CONFIG_KEYS = (
    "data_window_start",
    "data_window_end",
    "review_bonus_started_date",
    "review_pool_effective_date",
)


def _read_config_tab(spreadsheet_id: str, token: str) -> dict[str, str]:
    """Read a Key/Value config tab into a flat dict. Returns {} if missing.

    Date-shaped keys (per ``_DATE_CONFIG_KEYS``) and any
    ``training_excluded:<name>`` rows are passed through
    ``coerce_iso_date`` so callers always see canonical ISO regardless
    of whether the sheet cell drifted to a serial or carries a
    leading apostrophe.
    """
    rng = urllib.parse.quote("config!A1:C500", safe="!:")
    url = f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{rng}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 400:  # tab missing
            return {}
        raise
    out: dict[str, str] = {}
    for row in data.get("values", []):
        if row and len(row) >= 2:
            key = row[0]
            raw = row[1]
            if key in _DATE_CONFIG_KEYS or (
                isinstance(key, str) and key.startswith("training_excluded:")
            ):
                coerced = coerce_iso_date(raw)
                if coerced is not None:
                    out[key] = coerced
                    continue
                # Leave the raw value in place so the caller can
                # produce a useful error message ("not ISO-date: 'banana'").
            out[key] = raw
    return out


def _resolve_data_window_end(model_cfg: dict[str, str]) -> datetime.date:
    """Pure helper — parse ``data_window_end`` from the model config dict.

    Extracted from ``process_reviews.main`` so it can be unit-tested
    without standing up a real Sheets read. ``model_cfg`` is the dict
    returned by ``_read_config_tab`` — by that point date-shaped keys
    have already been routed through ``coerce_iso_date``, so the value
    here is either canonical ISO ("2026-05-20") or the raw cell value
    when even ``coerce_iso_date`` couldn't recover it.

    Raises ``RuntimeError`` with a message that includes the literal
    bad cell value (so the operator can search the sheet for it) if
    the value cannot be parsed.
    """
    raw = (model_cfg.get("data_window_end") or "")
    if isinstance(raw, str):
        raw = raw.strip()
    if not raw:
        raise RuntimeError(
            "bhaga_model > config has no data_window_end. "
            "Run update_model_sheet first."
        )
    # _read_config_tab already coerced; defensively try again so the
    # helper is directly callable with raw dicts in tests.
    coerced = coerce_iso_date(raw)
    if coerced is None:
        raise RuntimeError(
            f"bhaga_model > config.data_window_end is not ISO-date: {raw!r}"
        )
    return datetime.date.fromisoformat(coerced)


def _read_training_excluded(spreadsheet_id: str, token: str) -> dict[str, datetime.date]:
    """Pull training_excluded:<name> entries from the Model sheet config tab."""
    cfg = _read_config_tab(spreadsheet_id, token)
    out: dict[str, datetime.date] = {}
    prefix = "training_excluded:"
    for k, v in cfg.items():
        if not k.startswith(prefix) or not v.strip():
            continue
        name = k[len(prefix):].strip()
        try:
            out[name] = datetime.date.fromisoformat(v.strip())
        except ValueError:
            print(f"  [training-read] unparseable date for {name!r}: {v!r}")
    return out


# ── Review message parsing ────────────────────────────────────────────


_FIELD_RE = re.compile(
    r"^\*\s+\*\*(?P<key>[^:*]+?):\*\*\s*(?P<val>.*?)$",
    re.MULTILINE,
)


def _extract_fields(content: str) -> dict[str, str]:
    """Parse `*   **<key>:** <value>` blocks where the value may span multiple lines.

    The ClickUp review-poster sometimes formats reviews like:

        *   **Comment:** Great bowl choices! I loved it

        Thank you Emily, Miles and Lavette. Really sweet girls

        *   **Google Reviews Page:** [...]

    The single-line `_FIELD_RE` would capture only "Great bowl choices! I loved it"
    and drop the second paragraph (where the named-shoutouts live!). This
    function instead finds each field-marker and treats the value as the span
    from that marker to the NEXT field-marker (or end of message), with
    blank-line and stray-* stripping.
    """
    matches = list(_FIELD_RE.finditer(content))
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        key = m.group("key").strip().lower()
        first_line_val = m.group("val").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        continuation = content[start:end]
        # Strip stray markdown bullets / horizontal rules on continuation lines
        # so we don't pollute the captured value.
        cont_lines = [
            ln.strip() for ln in continuation.split("\n")
            if ln.strip() and not ln.strip().startswith(("---", "===", "***"))
        ]
        if first_line_val:
            parts = [first_line_val] + cont_lines
        else:
            parts = cont_lines
        out[key] = " ".join(p for p in parts if p).strip()
    return out


def _is_review_message(content: str) -> bool:
    """A message is a candidate review if it starts with the standard header."""
    return content.lstrip().startswith(REVIEW_HEADER)


def _is_held_back_review(content: str, ts_ms: int, window_end_ts_ms: int) -> bool:
    """True iff a message is a genuine review posted after end-of-day CT on
    data_window_end (so it is held back, not operational chatter).

    The held-back counter must only count actual review-bot posts — the ClickUp
    channel also carries duty checklists, package photos, and team messages that
    must never inflate the counter (2026-06-25 incident: 11 chatter posts on both
    6/24 and 6/25 triggered HELD-BACK: 11 when 0 real reviews were deferred).
    """
    return _is_review_message(content) and ts_ms > window_end_ts_ms


def parse_review_message(
    *, message_id: str, post_ts_ms: int, content: str,
) -> Optional[dict]:
    """Parse a ClickUp review-bot post into structured fields.

    Returns a dict on success, or None if any required field is missing
    (caller should route that message to the unparseable tab).
    """
    fields = _extract_fields(content)

    time_str = fields.get("time of comment")
    rating_str = fields.get("rating")
    reviewer = fields.get("commented by")
    comment = fields.get("comment", "").strip()
    # Either label has been seen; accept both.
    url_field = fields.get("google reviews page") or fields.get("google review") or ""

    if not (time_str and rating_str and reviewer):
        return None

    # Parse "May 16, 2026 7:27 PM CT" -> aware datetime in CT.
    cleaned_time = time_str.replace(" CT", "").strip()
    post_dt_ct: Optional[datetime.datetime] = None
    for fmt in ("%B %d, %Y %I:%M %p", "%b %d, %Y %I:%M %p", "%B %d, %Y %H:%M"):
        try:
            naive = datetime.datetime.strptime(cleaned_time, fmt)
            post_dt_ct = naive.replace(tzinfo=CT)
            break
        except ValueError:
            continue
    if post_dt_ct is None:
        # If the bot's timestamp doesn't parse, fall back to the ClickUp post
        # epoch (still timezone-correct). Log it but don't drop the review.
        post_dt_ct = datetime.datetime.fromtimestamp(post_ts_ms / 1000, tz=CT)

    rating_token = rating_str.split()[0].strip().lower()
    rating = RATING_WORDS.get(rating_token)
    if rating is None:
        digits = "".join(c for c in rating_str if c.isdigit())
        rating = int(digits) if digits else None

    # Strip a markdown link wrapper from the URL if present:
    # "[Open Google Reviews](https://...)" -> "https://..."
    url_match = re.search(r"\(([^)]+)\)", url_field)
    review_url = url_match.group(1) if url_match else url_field.strip()

    # Stable review_id: hash post_ts + reviewer + comment-prefix.
    # Survives the same review being posted twice via different ClickUp
    # message ids (which would happen if the upstream automation re-fires).
    review_id = hashlib.sha1(
        f"{post_dt_ct.isoformat()}|{reviewer}|{comment[:64]}".encode("utf-8")
    ).hexdigest()[:16]

    return {
        "review_id": review_id,
        "clickup_message_id": message_id,
        "post_ts_ms": post_ts_ms,
        "post_dt_ct": post_dt_ct,
        "post_date_ct": post_dt_ct.date(),
        "post_time_ct": post_dt_ct.strftime("%H:%M"),
        "rating": rating,
        "reviewer": reviewer,
        "comment": comment,
        "review_url": review_url,
    }


# ── Shift assignment + name resolution ────────────────────────────────


def find_shift_for_post(
    post_dt: datetime.datetime, punches: list[dict],
) -> dict:
    """Determine which shift gets credit for a review posted at post_dt.

    Per the policy (re-stated):
      - If post-time is within an active punch on the post date -> in-hours,
        shift_date = post_date, shift_members = anyone clocked in at post-time.
      - Else -> "last shift" mode: find the most recent punch whose
        (date, out_time) is strictly before post_dt. shift_date = that
        punch's date. shift_members = everyone who worked any punch on
        shift_date.

    Returns:
      {
        "shift_date": "YYYY-MM-DD" or None (if no shifts found at all),
        "shift_members": [canonical_name, ...] (deduped),
        "all_employees_on_date": [canonical_name, ...] (for named-search),
        "assignment_reason": "in_hours" | "last_shift_same_day" |
                             "last_shift_prior_day" | "no_shift_found",
      }
    """
    post_date_iso = post_dt.date().isoformat()
    post_hhmm = post_dt.strftime("%H:%M")

    # Pass 1: in-hours match on the post date.
    in_hours_members: set[str] = set()
    for p in punches:
        if p["date"] != post_date_iso:
            continue
        if p["in_time"] <= post_hhmm <= p["out_time"]:
            in_hours_members.add(p["employee_name"])

    if in_hours_members:
        all_on_date = {p["employee_name"] for p in punches if p["date"] == post_date_iso}
        return {
            "shift_date": post_date_iso,
            "shift_members": sorted(in_hours_members),
            "all_employees_on_date": sorted(all_on_date),
            "assignment_reason": "in_hours",
        }

    # Pass 2: last-shift fallback. Find the most recent (date, out_time)
    # strictly before post_dt.
    best_key: Optional[tuple] = None
    for p in punches:
        end_dt_naive = datetime.datetime.fromisoformat(
            f"{p['date']}T{p['out_time']}:00"
        )
        end_dt = end_dt_naive.replace(tzinfo=CT)
        if end_dt >= post_dt:
            continue
        key = (p["date"], p["out_time"])
        if best_key is None or key > best_key:
            best_key = key

    if best_key is None:
        return {
            "shift_date": None,
            "shift_members": [],
            "all_employees_on_date": [],
            "assignment_reason": "no_shift_found",
        }

    shift_date = best_key[0]
    same_day = shift_date == post_date_iso
    on_date = {p["employee_name"] for p in punches if p["date"] == shift_date}
    return {
        "shift_date": shift_date,
        "shift_members": sorted(on_date),
        "all_employees_on_date": sorted(on_date),
        "assignment_reason": "last_shift_same_day" if same_day else "last_shift_prior_day",
    }


def _build_first_name_index(aliases: dict[str, str]) -> dict[str, list[str]]:
    """{first_name_lower: [canonical_names_with_that_first_name]}.

    Two-source build:

    1. Each canonical's own first name (e.g. "Guerrero, Amy" -> "amy").
    2. Each alias KEY's first token (e.g. raw "Miles" -> "Guerrero, Amy"
       contributes "miles" -> ["Guerrero, Amy"]). This is how nicknames,
       alternative spellings, and ADP raw-name variants get matched — used to
       resolve "Sebastian was great" -> ["Alvarez, Sebastian"] AND
       "Myles was nice" -> ["Guerrero, Amy"] when an alias "Myles"->"Guerrero,
       Amy" exists in the store profile.
    """
    out: dict[str, set[str]] = {}

    def _add(first_token: str, canonical: str) -> None:
        first = first_token.lower().strip(",.")
        if first:
            out.setdefault(first, set()).add(canonical)

    for canonical in set(aliases.values()):
        # canonical is "Last, First" -> first = "First"
        if "," in canonical:
            first = canonical.split(",", 1)[1].strip().split()[0]
        else:
            first = canonical.split()[0]
        _add(first, canonical)

    # Pass 2: alias keys. Raw alias keys can be "Guerrero Amy" (no comma -
    # use first token = "Guerrero" → BUT that's the last name, useless here),
    # or just a nickname like "Miles" (use the whole thing). Strategy:
    #   - If the alias key has a comma: skip last-name extraction here,
    #     canonical was already indexed by first name in pass 1.
    #   - If the alias key has no comma AND only one token: treat it as a
    #     nickname/alt-spelling (e.g. "Miles", "Myles", "Z").
    #   - If multi-token without comma: skip (it's likely ADP raw "Last First"
    #     which would pollute the index with last-names).
    for raw, canonical in aliases.items():
        if "," in raw:
            continue
        tokens = raw.split()
        if len(tokens) == 1:
            _add(tokens[0], canonical)

    return {k: sorted(v) for k, v in out.items()}


# Common English words that look like names but should never trigger a fuzzy
# match. Add to this list as false positives surface in production.
#
# IMPORTANT: "miles" is intentionally ABSENT — it is a customer-facing alias
# for Guerrero, Amy (and "myles" resolves to the same person). Before adding
# ANY short common-looking word, check the bhaga_model > employees aliases
# column; suppressing a real employee alias silently drops shoutout credit.
_FUZZY_MATCH_STOPWORDS = frozenset({
    "the", "and", "but", "for", "with", "from", "into", "very", "good", "great",
    "love", "loved", "nice", "best", "they", "this", "that", "here", "were",
    "have", "been", "will", "your", "their", "them", "made", "make", "menu",
    "well", "back", "down", "over", "much", "just", "more", "also", "some",
    "perfect", "amazing", "really", "always", "thanks", "thank",
})


def _levenshtein(a: str, b: str) -> int:
    """Iterative DP edit distance. Small strings only — comment tokens vs first names."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins = cur[j - 1] + 1
            dele = prev[j] + 1
            sub = prev[j - 1] + (ca != cb)
            cur.append(min(ins, dele, sub))
        prev = cur
    return prev[-1]


def _fuzzy_first_name_lookup(
    token: str, first_name_index: dict[str, list[str]],
) -> list[str]:
    """Return canonical-name candidates for a token that doesn't exact-match.

    Uses Levenshtein distance with a 25%-of-longer-string threshold:

      - max(token_len, name_len) <= 4 -> threshold 1
      - max ... 5-7                    -> threshold 2
      - max ... 8+                     -> threshold 3

    Examples (all real misspellings seen in production):
      "miles"   vs "Myles"   (max=5, dist=1)            -> match
      "Lizet"   vs "Lisette" (max=7, dist=2)            -> match
      "Lavette" vs "Lisette" (max=7, dist=2)            -> match
      "Emily"   vs "Emely"   (max=5, dist=2)            -> match
      "great"   vs "kate"    (max=5, dist=2; stopword)  -> rejected by _FUZZY_MATCH_STOPWORDS
      "amy"     vs "Amy"     (exact, handled by pass 1)

    Drops common English stopwords up front to avoid trivially-close noise.
    """
    if token in _FUZZY_MATCH_STOPWORDS or len(token) < 3:
        return []
    found: list[str] = []
    for first, canonicals in first_name_index.items():
        # Strong gate: same first letter. Misspellings of names virtually
        # always preserve the leading sound/letter ("Lizet"->"Lisette",
        # "miles"->"Myles", "Emily"->"Emely"). Drops the false-positive
        # surface drastically before we run edit-distance.
        if token[0] != first[0]:
            continue
        max_len = max(len(token), len(first))
        if max_len <= 4:
            threshold = 1
        elif max_len <= 6:
            threshold = 2
        else:
            threshold = 3
        if abs(len(token) - len(first)) > threshold:
            continue
        if _levenshtein(token, first) <= threshold:
            found.extend(canonicals)
    return list(dict.fromkeys(found))


def match_named_baristas(
    comment: str,
    *,
    eligible_employees: list[str],
    first_name_index: dict[str, list[str]],
) -> tuple[list[str], list[str]]:
    """Scan a comment for first-name mentions of employees who worked the shift date.

    Two-pass match:
      1. Exact case-insensitive first-name match (e.g. "Sebastian" -> "Alvarez, Sebastian").
      2. Fuzzy fallback (Levenshtein) for misspellings the customer typed
         (e.g. "Lizet"/"Lavette" -> "Padron, Lisette", "miles" -> "Mata, Myles",
         "Emily" -> "Urrutia, Emely"). Fuzzy matches are still restricted to
         employees who actually worked the relevant shift date, so the
         false-positive rate stays low.

    Returns (named_canonical_names, ambiguities).
      - named_canonical_names: resolved canonical names credited at $20.
      - ambiguities: descriptive strings for human review (e.g.
        "Daniel matches both Saldana, Daniel and Doe, Daniel").
    """
    if not comment:
        return [], []

    tokens = re.findall(r"[A-Za-z']{2,}", comment)
    eligible_set = set(eligible_employees)

    named: set[str] = set()
    ambiguities: list[str] = []
    seen_first_names: set[str] = set()

    for tok in tokens:
        first = tok.lower()
        if first in seen_first_names:
            continue

        # Pass 1: exact match.
        candidates = first_name_index.get(first, [])
        match_mode = "exact"

        # Pass 2: fuzzy fallback if exact didn't find anyone on-shift.
        if not candidates or not any(c in eligible_set for c in candidates):
            fuzzy = _fuzzy_first_name_lookup(first, first_name_index)
            if fuzzy:
                candidates = fuzzy
                match_mode = "fuzzy"

        if not candidates:
            continue

        on_shift_candidates = [c for c in candidates if c in eligible_set]
        if not on_shift_candidates:
            continue
        seen_first_names.add(first)
        if len(on_shift_candidates) == 1:
            named.add(on_shift_candidates[0])
            if match_mode == "fuzzy":
                ambiguities.append(
                    f"fuzzy match: {tok!r} -> {on_shift_candidates[0]} "
                    f"(verify in case of misspelling)"
                )
        else:
            ambiguities.append(
                f"{tok.title()} matches {len(on_shift_candidates)} on-shift "
                f"employees ({match_mode}): {', '.join(on_shift_candidates)}"
            )

    return sorted(named), ambiguities


# ── Bonus allocation ─────────────────────────────────────────────────


def split_pool_equally(pool_dollars: int, members: list[str]) -> dict[str, float]:
    """Split a fixed pool equally to the cent; remainder cents go to the
    alphabetically-first members so the result is deterministic and the
    shares sum to exactly pool_dollars."""
    uniq = sorted(set(members))
    n = len(uniq)
    if n == 0:
        return {}
    total_cents = int(round(pool_dollars * 100))
    base, rem = divmod(total_cents, n)
    return {emp: (base + (1 if i < rem else 0)) / 100.0 for i, emp in enumerate(uniq)}


def allocate_bonus(
    *,
    shift_members: list[str],
    named: list[str],
    excluded_permanent: set[str],
    training_through: dict[str, datetime.date],
    shift_date: str,
    post_date: datetime.date,
    assignment_reason: str,
    pool_effective_date: datetime.date,
    pool_dollars: int,
) -> dict[str, float]:
    """Per-employee bonus in dollars.

    Two date-bracketed modes:

    Pool mode (post_date >= pool_effective_date, i.e. on/after 2026-06-08):
        - A fixed pool of pool_dollars ($20) is split EQUALLY among all
          non-excluded in-hours shift members.
        - Requires assignment_reason == "in_hours"; if the review was posted
          outside an active shift, the pool is $0 (no fallback).
        - Permanent and training exclusions apply; named shoutouts are IGNORED
          (the named person gets the same equal share, not the old $20 flat).
        - Returns {canonical_name: dollars_as_float} where shares sum to
          exactly pool_dollars. Excluded employees are not in the output.

    Legacy mode (post_date < pool_effective_date):
        - Shoutout mode (named non-empty): ONLY the named people earn
          NAMED_BONUS_DOLLARS ($20 each). A shoutout OVERRIDES both
          permanent and training exclusions.
        - No-shoutout mode (generic 5★, no names): every non-excluded shift
          member earns BASE_BONUS_DOLLARS ($10). Exclusions apply.

    Returns {canonical_name: dollars}. Excluded employees who aren't rescued
    by a legacy shoutout are NOT included in the output dict.
    """

    def _is_excluded(emp: str) -> bool:
        if emp in excluded_permanent:
            return True
        last_training = training_through.get(emp)
        if last_training is not None and shift_date <= last_training.isoformat():
            return True
        return False

    # ── Pool mode (2026-06-08 and later) ─────────────────────────────
    if post_date >= pool_effective_date:
        if assignment_reason != "in_hours":
            return {}
        eligible = [e for e in shift_members if not _is_excluded(e)]
        return split_pool_equally(pool_dollars, eligible)

    # ── Legacy mode (before 2026-06-08) ──────────────────────────────
    # Build the unified roster so callers can pass the raw shift roster;
    # named shoutout pays named directly regardless of shift membership.
    out: dict[str, float] = {}
    named_set = set(named)
    members = sorted(set(shift_members) | named_set)

    if named_set:
        for emp in named_set:
            out[emp] = float(NAMED_BONUS_DOLLARS)
    else:
        for emp in members:
            if _is_excluded(emp):
                continue
            out[emp] = float(BASE_BONUS_DOLLARS)

    return out


# ── Pay-period helpers (mirror update_model_sheet's discover_periods) ──


def assign_to_period(date_iso: str, periods: list[dict]) -> Optional[dict]:
    """Find the period containing date_iso. Returns None if outside all periods."""
    for p in periods:
        if p["start"] <= date_iso <= p["end"]:
            return p
    return None


# ── Output: per-review row + per-period rollup ───────────────────────


REVIEW_HEADER_ROW = [
    "review_id", "post_ts_ct", "post_date_ct", "rating", "reviewer",
    "comment", "named_baristas", "named_status", "shift_date_credited",
    "shift_assignment_reason", "shift_members", "trainees_on_shift",
    "named_credit_each", "base_credit_each", "total_bonus",
    "review_url", "clickup_message_id", "ingested_at_utc",
]

UNPARSEABLE_HEADER_ROW = [
    "clickup_message_id", "post_ts_ms", "post_dt_ct", "content_preview",
    "ingested_at_utc",
]

REVIEW_PERIOD_HEADER_ROW = [
    "period_start", "period_end", "is_open", "employee",
    "reviews_credited", "named_count", "base_dollars", "named_dollars",
    "total_bonus", "likely_reason",
]


_LIST_SEP = "; "  # Multi-name columns use "; " — names contain commas (e.g. "Alvarez, Sebastian"), so comma is ambiguous as a separator.


def build_review_row(rec: dict) -> list:
    """Flatten a review record into a row for the `reviews` tab.

    Date columns are written with a leading "'" so USER_ENTERED stores them
    as plain text (otherwise Sheets coerces "2026-05-11" -> serial 46153
    and breaks all downstream ISO-string comparisons).

    Multi-name columns (named, shift_members, trainees) use "; " as the
    separator, NOT ", " — because each canonical name already contains a
    comma (e.g. "Alvarez, Sebastian"). Splitting on "," would create
    phantom employees like "Alvarez" and "Sebastian" in the rollup.
    """
    def _txt_date(s: str) -> str:
        return ("'" + s) if s else ""

    shift_credited = rec.get("shift_date_credited") or ""
    # Pool mode: post_date_ct >= POOL_EFFECTIVE_DATE (a datetime.date at this point).
    pool_mode = rec["post_date_ct"] >= POOL_EFFECTIVE_DATE
    has_shoutout = (not pool_mode) and bool(rec.get("named"))
    has_payout = bool(rec.get("shift_members_credited"))
    # named_credit_each: $20 for legacy shoutout; "" for pool or no-shoutout.
    # base_credit_each: per-head pool share (display) for pool; $10 for legacy no-shoutout; "" for legacy shoutout.
    if pool_mode:
        named_credit_display = ""
        if has_payout and rec.get("allocations"):
            base_credit_display = round(POOL_DOLLARS / len(rec["allocations"]), 2)
        else:
            base_credit_display = ""
    else:
        named_credit_display = NAMED_BONUS_DOLLARS if (has_shoutout and has_payout) else ""
        base_credit_display = BASE_BONUS_DOLLARS if (has_payout and not has_shoutout) else ""
    return [
        rec["review_id"],
        "'" + rec["post_dt_ct"].isoformat(),
        _txt_date(rec["post_date_ct"].isoformat()),
        rec.get("rating") or "",
        rec["reviewer"],
        rec["comment"],
        _LIST_SEP.join(rec.get("named", [])),
        rec.get("named_status", "ok"),
        _txt_date(shift_credited),
        rec.get("shift_assignment_reason", ""),
        _LIST_SEP.join(rec.get("shift_members_credited", [])),
        _LIST_SEP.join(rec.get("trainees_on_shift", [])),
        named_credit_display,
        base_credit_display,
        rec.get("total_bonus_dollars", 0),
        rec.get("review_url", ""),
        rec.get("clickup_message_id", ""),
        rec.get("ingested_at_utc", ""),
    ]


def build_period_rollup(
    reviews: list[dict], periods: list[dict],
) -> list[list]:
    """Per-employee per-period bonus rollup (mirrors tip_alloc_period shape)."""
    # period_key -> employee -> stats
    grid: dict[tuple, dict[str, dict]] = {}
    for r in reviews:
        if not r.get("shift_date_credited"):
            continue
        if r.get("rating") != 5:
            continue
        period = assign_to_period(r["shift_date_credited"], periods)
        if period is None:
            continue
        key = (period["start"], period["end"], period.get("is_open", False))
        bucket = grid.setdefault(key, {})
        named_set = set(r.get("named", []))
        for emp, dollars in r.get("allocations", {}).items():
            slot = bucket.setdefault(emp, {
                "reviews_credited": 0, "named_count": 0,
                "base_dollars": 0, "named_dollars": 0,
            })
            slot["reviews_credited"] += 1
            if emp in named_set:
                slot["named_count"] += 1
                slot["named_dollars"] += dollars
            else:
                slot["base_dollars"] += dollars

    rows: list[list] = [REVIEW_PERIOD_HEADER_ROW]
    for (pstart, pend, is_open), bucket in sorted(grid.items()):
        for emp in sorted(bucket.keys()):
            s = bucket[emp]
            total = s["base_dollars"] + s["named_dollars"]
            if total == 0:
                continue
            if is_open:
                reason = "Open period — finalize at pay close"
            elif s["named_count"] / max(s["reviews_credited"], 1) > 0.5:
                reason = "High named-shoutout rate"
            elif s["named_count"] == 0 and pstart >= POOL_EFFECTIVE_DATE.isoformat():
                reason = "Review pool ($20 split)"
            elif s["named_count"] == 0:
                reason = "Standard bonuses"
            else:
                reason = "Mixed (named + base)"
            rows.append([
                _iso_date_for_sheet_cell(pstart),
                _iso_date_for_sheet_cell(pend),
                "yes" if is_open else "no", emp,
                s["reviews_credited"], s["named_count"],
                s["base_dollars"], s["named_dollars"], total, reason,
            ])
    return rows


def rebuild_review_bonus_period(
    *,
    model_sid: str,
    token: str,
    all_reviews: list[dict],
    data_window_end: datetime.date,
    profile: dict,
) -> int:
    """Rebuild ``review_bonus_period`` from reviews + algorithmic pay periods.

    Period boundaries come from ``discover_periods`` (store-profile anchor),
    not from ADP Earnings exports. Returns the number of data rows written
    (excluding the header).
    """
    from agents.bhaga.scripts.update_model_sheet import (  # noqa: PLC0415
        append_open_period,
        discover_periods,
    )

    window_end_iso = data_window_end.isoformat()
    periods = discover_periods(
        anchor_end_date=profile["adp_run"]["pay_periods_anchor_end_date"],
        pay_frequency=profile["adp_run"].get("pay_frequency", ""),
        data_start=profile["calibration"]["first_data_window"]["start"],
        last_data_date=window_end_iso,
    )
    periods = append_open_period(periods, last_data_date=window_end_iso)
    rollup_rows = build_period_rollup(all_reviews, periods)

    # ── BQ sink (canonical write, non-fatal) ─────────────────────────────────
    # When BHAGA_DATASTORE=bigquery, persist the same rollup to BQ so
    # model_review_bonus_period is always the canonical source and the Grafana
    # payroll view (vw_model_payroll_period) can join against it.
    # Errors are logged as a breadcrumb but never fail the Sheet write.
    if _bq_enabled():
        try:
            from agents.bhaga.scripts.materialize_model_bq import load_model_rows  # noqa: PLC0415
            # replace=True: this rollup is a FULL rebuild (every period/employee),
            # so truncate-then-load mirrors the Sheet's clear-and-write and prevents
            # ghost rows from periods/employees that dropped out of the rebuild.
            n_bq = load_model_rows("model_review_bonus_period", rollup_rows, replace=True)
            print(f"  [review_bonus_period→BQ] {n_bq} rows written (full replace) into model_review_bonus_period")
        except Exception as exc:  # noqa: BLE001
            print(f"  [review_bonus_period→BQ] WARNING: BQ write failed (Sheet is unaffected): {exc}")

    # ── Sheet sink (legacy write, always runs) ────────────────────────────────
    sheet_id = add_sheet_if_missing(
        model_sid, token, tab_name="review_bonus_period", column_count=12,
    )
    clear_and_write_tab(
        model_sid, token, tab_name="review_bonus_period", values=rollup_rows,
    )
    bold_header_row(model_sid, token, sheet_id=sheet_id)
    format_currency_columns(
        model_sid, token, sheet_id=sheet_id, column_indices=[6, 7, 8],
    )
    return len(rollup_rows) - 1


# ── Main orchestration ───────────────────────────────────────────────


def fetch_review_messages(
    *,
    since_ts_ms: int,
    max_pages: int = 40,
) -> list[dict]:
    """Fetch review messages from ClickUp (stateless, no Sheets dependency).

    Returns raw ClickUp message dicts. Safe to call from a background thread
    during parallel data gathering — no Google Sheets or profile I/O. The
    orchestrator's ``_run_review_fetch`` thread calls this, caches the result
    to JSON, and later passes that file to ``main`` via ``--prefetched-messages``.
    """
    return fetch_messages(
        REVIEW_CHANNEL_ID, team_id=CLICKUP_TEAM_ID,
        since_ts_ms=since_ts_ms, max_pages=max_pages,
    )


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", default="palmetto")
    cli.add_argument(
        "--since", default=None,
        help="Override start date for fetch (YYYY-MM-DD). Default: read "
             "last_processed_ts_ms from Review Raw config tab; if missing, "
             "use the bonus_start_date (2026-05-11).",
    )
    cli.add_argument(
        "--until", default=None,
        help="End cap for review processing (YYYY-MM-DD). Reviews posted "
             "after end-of-day CT on this date are skipped. Default: "
             "data_window_end from the model config tab.",
    )
    cli.add_argument("--max-pages", type=int, default=40,
                     help="Cap on ClickUp pagination depth (default 40).")
    cli.add_argument(
        "--prefetched-messages", default=None, metavar="PATH",
        help="Path to a JSON file containing pre-fetched ClickUp messages. "
             "Skips the ClickUp API fetch and reads from this file instead. "
             "Used by the orchestrator to pass messages fetched in parallel.",
    )
    cli.add_argument("--dry-run", action="store_true",
                     help="Parse + compute but do NOT write to sheets or Slack.")
    cli.add_argument("--no-slack", action="store_true")
    args = cli.parse_args()

    if args.no_slack:
        os.environ["BHAGA_SLACK_DISABLED"] = "1"

    profile = _load_profile(args.store)
    # Aliases + exclusions now come from the sheet (bhaga_model > employees +
    # bhaga_model > config). The local JSON is only the bootstrap pointer.
    from skills.store_profile import load_aliases, load_exclusions  # local import to avoid module-load circulars
    aliases = load_aliases(args.store)
    _excl_sheet = load_exclusions(args.store)
    excluded_permanent = set(_excl_sheet["permanent"])
    model_sid = resolve_sheet_id("bhaga_model", profile)

    raw_sheet_cfg = profile["google_sheets"].get("bhaga_review_raw")
    if not raw_sheet_cfg or not raw_sheet_cfg.get("spreadsheet_id"):
        print(
            "ERROR: profile is missing google_sheets.bhaga_review_raw.spreadsheet_id. "
            "Create the sheet first (one-time) and add it to the profile."
        )
        return 2
    raw_sid = resolve_sheet_id("bhaga_review_raw", profile)

    token = refresh_access_token(args.store)

    # ── Read tunable constants from store_config BQ (BQ-canonical). ──
    global BASE_BONUS_DOLLARS, NAMED_BONUS_DOLLARS, BONUS_START_DATE, POOL_EFFECTIVE_DATE, POOL_DOLLARS
    from core.store_config import get_config as _get_cfg

    def _get_date_cfg(key: str) -> str | None:
        return (_get_cfg(args.store, key) or "").strip() or None

    def _get_dollar_cfg(key: str, default: int) -> int:
        s = (_get_cfg(args.store, key) or "").strip()
        if not s:
            return default
        try:
            return int(float(s))
        except ValueError:
            print(f"WARN: store_config.{key} is not numeric ({s!r}); using default {default}.")
            return default

    bonus_start_raw = _get_date_cfg(MODEL_CFG_BONUS_START)
    if bonus_start_raw:
        try:
            BONUS_START_DATE = datetime.date.fromisoformat(bonus_start_raw)
        except ValueError:
            print(f"WARN: store_config.{MODEL_CFG_BONUS_START} is not ISO "
                  f"({bonus_start_raw!r}); falling back to module default.")

    pool_effective_raw = _get_date_cfg(MODEL_CFG_POOL_EFFECTIVE)
    if pool_effective_raw:
        try:
            POOL_EFFECTIVE_DATE = datetime.date.fromisoformat(pool_effective_raw)
        except ValueError:
            print(f"WARN: store_config.{MODEL_CFG_POOL_EFFECTIVE} is not ISO "
                  f"({pool_effective_raw!r}); falling back to module default.")

    BASE_BONUS_DOLLARS = _get_dollar_cfg(MODEL_CFG_BASE_BONUS, BASE_BONUS_DOLLARS)
    NAMED_BONUS_DOLLARS = _get_dollar_cfg(MODEL_CFG_NAMED_BONUS, NAMED_BONUS_DOLLARS)
    POOL_DOLLARS = _get_dollar_cfg(MODEL_CFG_POOL_DOLLARS, POOL_DOLLARS)

    # ── Resolve incremental anchor: derive from the reviews tab itself. ──
    # The data IS the state. No separate config row needed: the latest
    # post_ts_ms we've already ingested IS our high-water mark.
    if args.since:
        since_dt = datetime.datetime.fromisoformat(args.since).replace(tzinfo=CT)
        since_ts_ms = int(since_dt.timestamp() * 1000) - 1
        source = f"--since override ({args.since})"
    else:
        latest_in_bq_ms = _latest_review_ts_ms()
        if latest_in_bq_ms is not None:
            since_ts_ms = latest_in_bq_ms
            source = f"google_reviews BQ max(post_ts_ct) [{latest_in_bq_ms} ms]"
        else:
            bonus_start_dt = datetime.datetime.combine(
                BONUS_START_DATE, datetime.time.min, tzinfo=CT,
            )
            since_ts_ms = int(bonus_start_dt.timestamp() * 1000) - 1
            source = f"empty google_reviews BQ -> bonus_start_date ({BONUS_START_DATE})"

    from agents.bhaga.scripts.model_inputs import read_training_excluded as _read_training_excluded_bq
    training_through = _read_training_excluded_bq(args.store)

    # Sync to the model's data_window_end so reviews stay aligned with the
    # rest of the workbook. Any review posted AFTER end-of-day CT on
    # data_window_end is held back (not written, not credited) until the
    # nightly refresh advances the window. This way:
    #   - `reviews` tab dates ≤ model.data_window_end
    #   - `review_bonus_period` only rolls up credited reviews ≤ same date
    #   - high-water mark never advances past the window end, so tomorrow's
    #     run will re-fetch the held-back messages.
    # --until overrides data_window_end for historical backfills.
    if args.until:
        data_window_end = datetime.date.fromisoformat(args.until)
        print(f"# --until override: data_window_end={data_window_end}")
    else:
        # BQ-canonical: derive from MAX(square_transactions.date_local).
        # data_window_end is a DERIVED value — never read from store_config
        # (a stale stored value would freeze the review crediting window;
        # see 2026-06-15 incident and core.store_config._DERIVED_KEYS).
        from core.store_config import resolve_data_window_end as _resolve_dwe
        max_date = _resolve_dwe(args.store) or ""
        if not max_date:
            print("ERROR: could not resolve data_window_end from BQ (square_transactions is empty or unavailable).")
            return 2
        try:
            data_window_end = datetime.date.fromisoformat(max_date)
        except ValueError:
            print(f"ERROR: data_window_end from BQ is not ISO-date: {max_date!r}")
            return 2
        print(f"# data_window_end (derived from MAX square_transactions): {data_window_end}")
    end_of_window_dt = datetime.datetime.combine(
        data_window_end, datetime.time.max, tzinfo=CT,
    )
    window_end_ts_ms = int(end_of_window_dt.timestamp() * 1000)
    print(f"# model data_window_end: {data_window_end} "
          f"(reviews after {end_of_window_dt.isoformat()} held back)")

    # Punches come from BQ adp_punches (BQ-canonical path).
    from agents.bhaga.scripts.model_inputs import read_punches_bq
    print(f"# loading punches from BQ adp_punches")
    punches = read_punches_bq(args.store)
    if not punches:
        print("ERROR: BQ adp_punches is empty. Run backfill_from_downloads.py first.")
        return 2

    # Re-resolve employee names through the alias map so raw-sheet names
    # written before alias corrections still match the canonical names
    # used by _build_first_name_index / eligible_set.
    for rec in punches:
        for key in ("employee_name", "employee_id"):
            if key in rec:
                rec[key] = normalize_employee_name(rec[key], aliases)

    print(f"#   → {len(punches)} punches")

    print(f"\n{'='*60}")
    print(f"BHAGA process_reviews  store={args.store}")
    print(f"  since_ts source:   {source}")
    print(f"  since_ts_ms:       {since_ts_ms}  "
          f"({datetime.datetime.fromtimestamp(since_ts_ms / 1000, tz=CT).isoformat()})")
    print(f"  training excluded: {len(training_through)} employee(s)")
    print(f"  permanent excl:    {sorted(excluded_permanent)}")
    print(f"  dry_run:           {args.dry_run}")
    print(f"{'='*60}")

    info_ping(f"process_reviews starting (since {datetime.datetime.fromtimestamp(since_ts_ms/1000, tz=CT).date()})")

    # Fetch new messages (or load from pre-fetched file).
    if args.prefetched_messages:
        prefetch_path = pathlib.Path(args.prefetched_messages)
        with prefetch_path.open() as fh:
            msgs = json.load(fh)
        print(f"# loaded {len(msgs)} pre-fetched messages from {prefetch_path.name}")
    else:
        msgs = fetch_review_messages(
            since_ts_ms=since_ts_ms, max_pages=args.max_pages,
        )
        print(f"# fetched {len(msgs)} new messages from #{REVIEW_CHANNEL_NAME}")

    first_name_index = _build_first_name_index(aliases)

    parsed_reviews: list[dict] = []
    unparseable_rows: list[list] = []
    anomalies: list[str] = []
    ingested_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    held_back = 0

    for m in msgs:
        msg_id = m.get("id", "")
        ts_ms = m.get("date") or 0
        content = m.get("content") or ""

        # Filter non-review messages first (duty checklists, package photos,
        # team chatter) so they never inflate the held-back counter.
        if not _is_review_message(content):
            continue

        # Hard cap at end-of-day CT for data_window_end. A genuine review posted
        # after that is held back: don't advance the high-water past it, don't
        # parse, don't write. Tomorrow's run (after the window advances) will
        # see this message again.
        if ts_ms > window_end_ts_ms:
            held_back += 1
            continue

        parsed = parse_review_message(
            message_id=msg_id, post_ts_ms=ts_ms, content=content,
        )
        if parsed is None:
            unparseable_rows.append([
                msg_id, ts_ms,
                datetime.datetime.fromtimestamp(ts_ms / 1000, tz=CT).isoformat(),
                content[:200].replace("\n", " | "),
                ingested_at,
            ])
            anomalies.append(f"unparseable review msg_id={msg_id} (ts={ts_ms})")
            continue

        # Shift assignment.
        shift_info = find_shift_for_post(parsed["post_dt_ct"], punches)
        # Named-search ONLY across employees who worked the assigned date
        # (the "whole-day for named" rule).
        eligible_for_named = shift_info["all_employees_on_date"]

        named, name_ambiguities = match_named_baristas(
            parsed["comment"],
            eligible_employees=eligible_for_named,
            first_name_index=first_name_index,
        )

        # Trainees on shift (informational column on the reviews tab).
        trainees_on_shift = [
            emp for emp in shift_info["shift_members"]
            if emp in training_through and (
                shift_info["shift_date"] is None
                or shift_info["shift_date"] <= training_through[emp].isoformat()
            )
        ]

        # Only allocate bonuses for 5-star reviews with comments.
        eligible_for_bonus = (
            parsed.get("rating") == 5
            and bool(parsed.get("comment"))
            and parsed["post_date_ct"] >= BONUS_START_DATE
        )

        if eligible_for_bonus and shift_info["shift_date"]:
            named_on_shift = [n for n in named if n in shift_info["shift_members"]]
            named_on_day_not_shift = [
                n for n in named if n not in shift_info["shift_members"]
            ]
            allocations = allocate_bonus(
                shift_members=shift_info["shift_members"],
                named=named,
                excluded_permanent=excluded_permanent,
                training_through=training_through,
                shift_date=shift_info["shift_date"],
                post_date=parsed["post_date_ct"],
                assignment_reason=shift_info["assignment_reason"],
                pool_effective_date=POOL_EFFECTIVE_DATE,
                pool_dollars=POOL_DOLLARS,
            )
            shift_members_credited = sorted(allocations.keys())
        else:
            allocations = {}
            shift_members_credited = []
            named_on_shift = []
            named_on_day_not_shift = []

        named_status = "ok"
        if name_ambiguities:
            named_status = "ambiguous: " + "; ".join(name_ambiguities)
            anomalies.append(
                f"ambiguous name match in review_id={parsed['review_id']}: {name_ambiguities}"
            )
        if eligible_for_bonus and not shift_info["shift_date"]:
            anomalies.append(
                f"no shift found for review_id={parsed['review_id']} posted at "
                f"{parsed['post_dt_ct'].isoformat()}"
            )

        rec = {
            **parsed,
            "named": named,
            "named_status": named_status,
            "shift_date_credited": shift_info["shift_date"] if eligible_for_bonus else None,
            "shift_assignment_reason": shift_info["assignment_reason"],
            "shift_members_credited": shift_members_credited,
            "trainees_on_shift": trainees_on_shift,
            "allocations": allocations,
            "total_bonus_dollars": sum(allocations.values()),
            "ingested_at_utc": ingested_at,
        }
        parsed_reviews.append(rec)

        # Negative-feedback canary: <=3 stars.
        if parsed.get("rating") and parsed["rating"] <= 3:
            anomalies.append(
                f"low-rating ({parsed['rating']}★) review by {parsed['reviewer']} "
                f"posted {parsed['post_dt_ct'].isoformat()}: "
                f"\"{parsed['comment'][:160]}\""
            )

    print(f"# parsed: {len(parsed_reviews)} reviews | unparseable: {len(unparseable_rows)} "
          f"| anomalies: {len(anomalies)} | held-back (post-window): {held_back}")
    for rec in parsed_reviews:
        print(f"  [{rec['post_dt_ct'].isoformat()}] {rec['rating']}★ "
              f"{rec['reviewer']:30.30s}  shift={rec['shift_date_credited']}  "
              f"members={','.join(rec['shift_members_credited']) or '-'}  "
              f"named={','.join(rec['named']) or '-'}  total=${rec['total_bonus_dollars']}")

    if args.dry_run:
        print("\nDRY RUN — no BQ writes, no sheet writes, no Slack.")
        return 0

    # ── Write google_reviews to BQ (primary sink) ──
    # BQ is the system of record. The reviews Sheet tab is rendered afterward
    # by render_raw_sheet_from_bq.py (reviews spec). load_rows is idempotent
    # via merge_keys=["review_id"].
    bq_review_rows = [_rec_to_bq_shape(r) for r in parsed_reviews]
    bq_review_rows = [r for r in bq_review_rows if r.get("review_id")]
    # google_reviews is BQ-primary. If the BQ client is unavailable (e.g.
    # BHAGA_DATASTORE != bigquery) load_rows silently returns 0 and the reviews
    # vanish — a confusing "parsed N, upserted 0". Fail loudly instead of
    # silently dropping rows that were successfully parsed.
    from core.datastore import get_client as _get_bq_client  # noqa: PLC0415
    if bq_review_rows and _get_bq_client() is None:
        print(
            "ERROR: parsed reviews but the BigQuery client is unavailable — "
            "google_reviews is BQ-primary. Set BHAGA_DATASTORE=bigquery so the "
            "reviews actually persist (refusing to silently drop "
            f"{len(bq_review_rows)} parsed review(s)).",
            file=sys.stderr,
        )
        return 3
    n_bq_loaded = 0
    if bq_review_rows:
        n_bq_loaded = load_rows(
            "google_reviews", bq_review_rows,
            merge_keys=["review_id"],
            column_bq_types={"ingested_at_utc": "TIMESTAMP"},
        )
    print(f"# google_reviews (BQ): {n_bq_loaded} rows upserted "
          f"({len(bq_review_rows)} parsed reviews)")

    # ── Write unparseable tab (operator triage surface, not mirrored to BQ) ──
    _ensure_review_raw_tabs(raw_sid, token)
    if unparseable_rows:
        _append_rows(raw_sid, token, tab="unparseable", rows=unparseable_rows)

    # Note: there is intentionally NO config tab on bhaga_review_raw.
    # The incremental high-water mark is derived from google_reviews BQ on
    # each run; the bonus constants and bonus_started_date live in
    # bhaga_model > config. Architecture rule: one config, on the model.

    # ── Rebuild review_bonus_period on Model sheet ──
    # Pull the FULL reviews tab back, then clamp to data_window_end so the
    # rollup never includes credited-shifts past what the rest of the model
    # has published. Belt-and-braces — the message-loop cap above should
    # already prevent any post-window review from making it into the raw
    # tab, but a manual backfill or human edit could still introduce one.
    all_reviews = _read_all_reviews(
        excluded_permanent=excluded_permanent,
        training_through=training_through,
    )
    pre_clip = len(all_reviews)
    window_end_iso = data_window_end.isoformat()
    all_reviews = [
        r for r in all_reviews
        if not r.get("shift_date_credited") or r["shift_date_credited"] <= window_end_iso
    ]
    if len(all_reviews) != pre_clip:
        print(f"# rollup clip: dropped {pre_clip - len(all_reviews)} review(s) "
              f"with shift_date_credited > {window_end_iso}")
    print(f"# Model rollup source: {len(all_reviews)} reviews "
          f"(window ≤ {window_end_iso}).")

    n_rollup = rebuild_review_bonus_period(
        model_sid=model_sid,
        token=token,
        all_reviews=all_reviews,
        data_window_end=data_window_end,
        profile=profile,
    )
    print(f"# review_bonus_period: {n_rollup} data rows.")

    # ── Slack summary ──
    # Build the summary line-by-line so HELD-BACK is prominent. The
    # `held_back` count is genuine reviews (not chatter) posted AFTER the
    # model's data_window_end — they are waiting for upstream data to advance
    # the window. Counting only reviews prevents operational channel messages
    # (duty checklists, package photos) from inflating this figure.
    parts = [f"Reviews: +{n_bq_loaded} BQ upserted (master now {len(all_reviews)} in BQ)"]
    if held_back > 0:
        parts.append(
            f"HELD-BACK: {held_back} "
            f"(model.data_window_end={end_of_window_dt.date().isoformat()} — "
            f"upstream data didn't advance; reviews will land on next "
            f"successful daily refresh)"
        )
    if unparseable_rows:
        parts.append(f"unparseable: +{len(unparseable_rows)}")
    if anomalies:
        parts.append(f"anomalies: {len(anomalies)}")
    summary = "; ".join(parts)
    review_anomaly_alert(anomalies)
    success_heartbeat(
        date=datetime.datetime.now(CT).date().isoformat(),
        tabs_written=2,
        runtime_s=0.0,
        extra=summary,
    )
    print(f"\nDONE. {summary}")
    return 0


# ── BQ helpers ───────────────────────────────────────────────────────


def _rec_to_bq_shape(rec: dict) -> dict:
    """Map a parsed review rec dict to the google_reviews BQ row shape.

    Uses build_review_row to produce the canonical sheet-order list, then
    strips text-protection apostrophes from date/ts fields before calling
    map_google_review for type coercion.
    """
    row = build_review_row(rec)
    sheet_dict = dict(zip(REVIEW_HEADER_ROW, row))
    for col in ("post_ts_ct", "post_date_ct", "shift_date_credited"):
        v = sheet_dict.get(col)
        if isinstance(v, str):
            sheet_dict[col] = v.lstrip("'")
    return map_google_review(sheet_dict)


# ── Sheet I/O helpers (Review Raw specific) ──────────────────────────


def _ensure_review_raw_tabs(spreadsheet_id: str, token: str) -> None:
    """Create the unparseable tab if missing.

    The reviews tab is no longer created here — it is rendered from BQ by
    render_raw_sheet_from_bq.py (reviews spec) after process_reviews completes.

    No config tab — by architecture, all config lives in bhaga_model. The
    incremental high-water mark is derived from google_reviews BQ on each run.
    """
    sid_unparseable = add_sheet_if_missing(spreadsheet_id, token, tab_name="unparseable",
                                           column_count=len(UNPARSEABLE_HEADER_ROW))
    if not _tab_has_any_data(spreadsheet_id, token, "unparseable"):
        clear_and_write_tab(spreadsheet_id, token, tab_name="unparseable",
                            values=[UNPARSEABLE_HEADER_ROW])
        bold_header_row(spreadsheet_id, token, sheet_id=sid_unparseable)


def _tab_has_any_data(spreadsheet_id: str, token: str, tab: str) -> bool:
    rng = urllib.parse.quote(f"{tab}!A1:A2", safe="!:")
    url = f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{rng}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return bool(data.get("values"))
    except urllib.error.HTTPError:
        return False


def _read_existing_review_ids(spreadsheet_id: str, token: str) -> set[str]:
    rng = urllib.parse.quote("reviews!A2:A100000", safe="!:")
    url = f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{rng}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError:
        return set()
    return {row[0] for row in data.get("values", []) if row and row[0]}


def _split_names(blob: str) -> list[str]:
    """Split a multi-name cell back into canonical names.

    New format uses "; " as the separator. Legacy format used ", " — but
    canonical names contain a comma themselves (e.g. "Alvarez, Sebastian"),
    so plain comma-split shatters each name into [last, first] phantom
    employees. If we see semicolons we use those; otherwise we re-pair the
    comma-split tokens by assuming every two tokens form one canonical
    name (the format produced by `, `.join(["Last, First", ...])).
    """
    if not blob:
        return []
    if ";" in blob:
        return [t.strip() for t in blob.split(";") if t.strip()]
    parts = [t.strip() for t in blob.split(",") if t.strip()]
    if not parts:
        return []
    # Legacy comma-joined format: pair every two tokens into "last, first".
    # If the count is odd we fall back to treating each token as its own
    # name (no good answer for malformed legacy data).
    if len(parts) % 2 == 0:
        return [f"{parts[i]}, {parts[i + 1]}" for i in range(0, len(parts), 2)]
    return parts


def _latest_review_ts_ms() -> Optional[int]:
    """Return the latest post_ts_ct epoch-ms from the google_reviews BQ table.

    This IS the incremental high-water mark — BQ is the system of record for
    reviews. post_ts_ct is stored as STRING (CT ISO with DST offset), so we
    parse each value in Python rather than relying on lexicographic SQL MAX.
    Returns None for an empty table or when BQ is unavailable.
    """
    if not _bq_enabled():
        return None
    try:
        rows = read_query(
            f"SELECT post_ts_ct FROM {fq('google_reviews')}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: could not read google_reviews high-water mark: {exc}")
        return None
    if not rows:
        return None
    latest: Optional[int] = None
    for row in rows:
        s = (row.get("post_ts_ct") or "").strip()
        if not s:
            continue
        try:
            dt = datetime.datetime.fromisoformat(s)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CT)
        ts_ms = int(dt.timestamp() * 1000)
        if latest is None or ts_ms > latest:
            latest = ts_ms
    return latest


def _sheets_serial_to_iso(val: str) -> Optional[str]:
    """Coerce a Sheets date serial (e.g. "46153") back into an ISO date.

    Older rows were written without leading-quote text protection, so dates
    got auto-coerced to Sheets serials. Returns None if val isn't a
    pure-integer serial; the caller should fall back to the raw string.
    """
    if not val:
        return None
    s = val.strip()
    if not s.isdigit():
        return None
    try:
        serial = int(s)
    except ValueError:
        return None
    # Sheets epoch: 1899-12-30 (matching Excel's bug-compat). Day 1 = 1899-12-31.
    epoch = datetime.date(1899, 12, 30)
    return (epoch + datetime.timedelta(days=serial)).isoformat()


def _read_all_reviews(
    *,
    excluded_permanent: set[str],
    training_through: dict[str, datetime.date],
) -> list[dict]:
    """Read all reviews from google_reviews BQ (primary source) for the rollup pass.

    Rebuilds per-row `allocations` using the CURRENT allocate_bonus policy so
    a policy change (e.g. the 2026-05-17 shoutout-only switch) takes effect on
    the next rollup without needing to rewrite any storage.

    BQ column names match the Sheet header keys consumed by the rollup logic:
    rating, named_baristas, shift_date_credited, shift_members, trainees_on_shift.
    shift_date_credited is a STRING in BQ (ISO date or ""); no serial coercion needed.
    """
    rows = read_query(
        f"SELECT * FROM {fq('google_reviews')}"
    )
    if not rows:
        return []
    out: list[dict] = []
    for bq_row in rows:
        d = dict(bq_row)
        # post_date_ct comes back as datetime.date from BQ — coerce to ISO string
        if isinstance(d.get("post_date_ct"), datetime.date):
            d["post_date_ct"] = d["post_date_ct"].isoformat()
        # rating: INT64 from BQ; coerce None to None (not 0)
        if d.get("rating") == 0:
            d["rating"] = None
        # shift_date_credited: STRING in BQ (clean ISO or ""); no serial coercion needed
        shift_date = str(d.get("shift_date_credited") or "").strip()
        d["shift_date_credited"] = shift_date or None
        d["named"] = _split_names(d.get("named_baristas") or "")
        members = _split_names(d.get("shift_members") or "")
        if d["shift_date_credited"]:
            pd_str = str(d.get("post_date_ct") or "").strip()
            try:
                post_date = datetime.date.fromisoformat(pd_str)
            except ValueError:
                post_date = BONUS_START_DATE  # unparseable → treat as legacy (safe default)
            d["allocations"] = allocate_bonus(
                shift_members=members,
                named=d["named"],
                excluded_permanent=excluded_permanent,
                training_through=training_through,
                shift_date=d["shift_date_credited"],
                post_date=post_date,
                assignment_reason=str(d.get("shift_assignment_reason") or ""),
                pool_effective_date=POOL_EFFECTIVE_DATE,
                pool_dollars=POOL_DOLLARS,
            )
            if post_date >= POOL_EFFECTIVE_DATE:
                d["named"] = []  # pool shares roll into base_dollars; named_count stays 0
        else:
            d["allocations"] = {}
        out.append(d)
    return out


def _append_rows(spreadsheet_id: str, token: str, *, tab: str, rows: list[list]) -> None:
    """Append rows to a tab (uses USER_ENTERED so dates render right)."""
    if not rows:
        return
    rng = urllib.parse.quote(f"{tab}!A1", safe="!:")
    url = (
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{rng}:append"
        f"?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
    )
    body = json.dumps({"values": rows}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        failure_alert(step="process_reviews", exception=exc)
        raise
