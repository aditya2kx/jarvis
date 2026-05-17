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
from core.config_loader import refresh_access_token  # noqa: E402
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

# Sheet-config keys (config tab of BHAGA Review Raw).
# bhaga_model > config keys for review-bonus tuning. These three are the
# only config keys this script reads. There is NO config tab on
# bhaga_review_raw — the incremental high-water mark is derived from the
# `reviews` tab itself (max post_ts_ct), not stored anywhere.
MODEL_CFG_BONUS_START = "review_bonus_started_date"
MODEL_CFG_BASE_BONUS = "review_base_bonus_dollars"
MODEL_CFG_NAMED_BONUS = "review_named_bonus_dollars"

# Marker that identifies an automated review post in the channel.
REVIEW_HEADER = "### Google Review"

# Word -> star count for the Rating field (covers both casing variants seen).
RATING_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
}


# ── Profile + sheet helpers ──────────────────────────────────────────


def _load_profile(store: str) -> dict:
    return json.loads((STORE_PROFILES / f"{store}.json").read_text())


def _read_config_tab(spreadsheet_id: str, token: str) -> dict[str, str]:
    """Read a Key/Value config tab into a flat dict. Returns {} if missing."""
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
            out[row[0]] = row[1]
    return out


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
    r"^\*\s+\*\*(?P<key>[^:*]+?):\*\*\s*(?P<val>.+?)\s*$",
    re.MULTILINE,
)


def _is_review_message(content: str) -> bool:
    """A message is a candidate review if it starts with the standard header."""
    return content.lstrip().startswith(REVIEW_HEADER)


def parse_review_message(
    *, message_id: str, post_ts_ms: int, content: str,
) -> Optional[dict]:
    """Parse a ClickUp review-bot post into structured fields.

    Returns a dict on success, or None if any required field is missing
    (caller should route that message to the unparseable tab).
    """
    fields: dict[str, str] = {}
    for m in _FIELD_RE.finditer(content):
        key = m.group("key").strip().lower()
        val = m.group("val").strip()
        fields[key] = val

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

    Used to resolve "Sebastian was great" -> ["Alvarez, Sebastian"].
    """
    out: dict[str, set[str]] = {}
    for canonical in set(aliases.values()):
        # canonical is "Last, First" -> first = "First"
        if "," in canonical:
            first = canonical.split(",", 1)[1].strip().split()[0].lower()
        else:
            first = canonical.split()[0].lower()
        out.setdefault(first, set()).add(canonical)
    return {k: sorted(v) for k, v in out.items()}


def match_named_baristas(
    comment: str,
    *,
    eligible_employees: list[str],
    first_name_index: dict[str, list[str]],
) -> tuple[list[str], list[str]]:
    """Scan a comment for first-name mentions of employees who worked the shift date.

    Returns (named_canonical_names, ambiguities).
      - named_canonical_names: resolved canonical names credited at $20.
      - ambiguities: descriptive strings for human review (e.g.
        "Daniel matches both Saldana, Daniel and Doe, Daniel").
    """
    if not comment:
        return [], []

    # Tokenize the comment loosely — words made of letters (and apostrophes).
    tokens = re.findall(r"[A-Za-z']{2,}", comment)
    eligible_set = set(eligible_employees)

    named: set[str] = set()
    ambiguities: list[str] = []
    seen_first_names: set[str] = set()

    for tok in tokens:
        first = tok.lower()
        if first in seen_first_names:
            continue
        candidates = first_name_index.get(first, [])
        if not candidates:
            continue
        # Restrict to employees who actually worked the relevant shift date.
        on_shift_candidates = [c for c in candidates if c in eligible_set]
        if not on_shift_candidates:
            continue
        seen_first_names.add(first)
        if len(on_shift_candidates) == 1:
            named.add(on_shift_candidates[0])
        else:
            ambiguities.append(
                f"{tok.title()} matches {len(on_shift_candidates)} on-shift "
                f"employees: {', '.join(on_shift_candidates)}"
            )

    return sorted(named), ambiguities


# ── Bonus allocation ─────────────────────────────────────────────────


def allocate_bonus(
    *,
    shift_members: list[str],
    named: list[str],
    excluded_permanent: set[str],
    training_through: dict[str, datetime.date],
    shift_date: str,
) -> dict[str, int]:
    """Per-employee bonus in dollars.

    Policy (updated 2026-05-17):

    Shoutout mode (named non-empty):
        - ONLY the named people earn the bonus ($20 each).
        - Non-named shift members earn $0 on a shoutout review.
        - A shoutout OVERRIDES both permanent and training exclusions:
          a customer calling you out by name credits you regardless of
          your normal exclusion status. (Example: manager Lindsay is
          excluded from the tip pool, but if a reviewer thanks her by
          name she earns the $20 shoutout bonus.) This is symmetric
          across the two exclusion lists.

    No-shoutout mode (generic 5★ praise, no names):
        - Every non-excluded shift member earns $10 base.
        - Permanent and training exclusions DO apply here, since there's
          no customer-specific signal directing the credit.

    Returns {canonical_name: dollars}. Excluded employees who aren't
    rescued by a shoutout are NOT included in the output dict.
    """
    out: dict[str, int] = {}
    named_set = set(named)

    def _is_excluded(emp: str) -> bool:
        if emp in excluded_permanent:
            return True
        last_training = training_through.get(emp)
        if last_training is not None and shift_date <= last_training.isoformat():
            return True
        return False

    if named_set:
        # Shoutout mode: pay every named person $20, ignoring exclusions.
        for emp in named_set:
            out[emp] = NAMED_BONUS_DOLLARS
    else:
        for emp in shift_members:
            if _is_excluded(emp):
                continue
            out[emp] = BASE_BONUS_DOLLARS

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
    has_shoutout = bool(rec.get("named"))
    has_payout = bool(rec.get("shift_members_credited"))
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
        # Under the new shoutout-only rule, base credit is only paid when
        # there are NO shoutouts. The audit columns reflect that.
        NAMED_BONUS_DOLLARS if (has_shoutout and has_payout) else "",
        BASE_BONUS_DOLLARS if (has_payout and not has_shoutout) else "",
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
            elif s["named_count"] == 0:
                reason = "Standard bonuses"
            else:
                reason = "Mixed (named + base)"
            rows.append([
                pstart, pend, "yes" if is_open else "no", emp,
                s["reviews_credited"], s["named_count"],
                s["base_dollars"], s["named_dollars"], total, reason,
            ])
    return rows


# ── Main orchestration ───────────────────────────────────────────────


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", default="palmetto")
    cli.add_argument(
        "--since", default=None,
        help="Override start date for fetch (YYYY-MM-DD). Default: read "
             "last_processed_ts_ms from Review Raw config tab; if missing, "
             "use the bonus_start_date (2026-05-11).",
    )
    cli.add_argument("--max-pages", type=int, default=40,
                     help="Cap on ClickUp pagination depth (default 40).")
    cli.add_argument("--dry-run", action="store_true",
                     help="Parse + compute but do NOT write to sheets or Slack.")
    cli.add_argument("--no-slack", action="store_true")
    args = cli.parse_args()

    if args.no_slack:
        os.environ["BHAGA_SLACK_DISABLED"] = "1"

    profile = _load_profile(args.store)
    aliases = profile["employees"]["aliases"]
    excluded_permanent = set(profile["employees"]["excluded_from_tip_pool_and_labor_pct"])
    model_sid = profile["google_sheets"]["bhaga_model"]["spreadsheet_id"]

    raw_sheet_cfg = profile["google_sheets"].get("bhaga_review_raw")
    if not raw_sheet_cfg or not raw_sheet_cfg.get("spreadsheet_id"):
        print(
            "ERROR: profile is missing google_sheets.bhaga_review_raw.spreadsheet_id. "
            "Create the sheet first (one-time) and add it to the profile."
        )
        return 2
    raw_sid = raw_sheet_cfg["spreadsheet_id"]

    token = refresh_access_token(args.store)

    # ── Read tunable constants from the MODEL config (single source of
    # truth — no config tab on raw sheets per architecture rule). ──
    global BASE_BONUS_DOLLARS, NAMED_BONUS_DOLLARS, BONUS_START_DATE
    model_cfg = _read_config_tab(model_sid, token)

    bonus_start_raw = (model_cfg.get(MODEL_CFG_BONUS_START) or "").strip()
    if bonus_start_raw:
        try:
            BONUS_START_DATE = datetime.date.fromisoformat(bonus_start_raw)
        except ValueError:
            print(f"WARN: bhaga_model > config.{MODEL_CFG_BONUS_START} is not ISO "
                  f"({bonus_start_raw!r}); falling back to module default.")

    def _read_dollar(key: str, default: int) -> int:
        s = (model_cfg.get(key) or "").strip()
        if not s:
            return default
        try:
            return int(float(s))
        except ValueError:
            print(f"WARN: bhaga_model > config.{key} is not numeric ({s!r}); "
                  f"using default {default}.")
            return default

    BASE_BONUS_DOLLARS = _read_dollar(MODEL_CFG_BASE_BONUS, BASE_BONUS_DOLLARS)
    NAMED_BONUS_DOLLARS = _read_dollar(MODEL_CFG_NAMED_BONUS, NAMED_BONUS_DOLLARS)

    # ── Resolve incremental anchor: derive from the reviews tab itself. ──
    # The data IS the state. No separate config row needed: the latest
    # post_ts_ms we've already ingested IS our high-water mark.
    if args.since:
        since_dt = datetime.datetime.fromisoformat(args.since).replace(tzinfo=CT)
        since_ts_ms = int(since_dt.timestamp() * 1000) - 1
        source = f"--since override ({args.since})"
    else:
        latest_in_sheet_ms = _latest_review_ts_ms(raw_sid, token)
        if latest_in_sheet_ms is not None:
            since_ts_ms = latest_in_sheet_ms
            source = f"reviews tab max(post_ts_ct) [{latest_in_sheet_ms} ms]"
        else:
            bonus_start_dt = datetime.datetime.combine(
                BONUS_START_DATE, datetime.time.min, tzinfo=CT,
            )
            since_ts_ms = int(bonus_start_dt.timestamp() * 1000) - 1
            source = f"empty reviews tab -> bonus_start_date ({BONUS_START_DATE})"

    training_through = _read_training_excluded(model_sid, token)

    # Sync to the model's data_window_end so reviews stay aligned with the
    # rest of the workbook. Any review posted AFTER end-of-day CT on
    # data_window_end is held back (not written, not credited) until the
    # nightly refresh advances the window. This way:
    #   - `reviews` tab dates ≤ model.data_window_end
    #   - `review_bonus_period` only rolls up credited reviews ≤ same date
    #   - high-water mark never advances past the window end, so tomorrow's
    #     run will re-fetch the held-back messages.
    model_cfg = _read_config_tab(model_sid, token)
    data_window_end_str = (model_cfg.get("data_window_end") or "").strip()
    if not data_window_end_str:
        print("ERROR: bhaga_model > config has no data_window_end. "
              "Run update_model_sheet first.")
        return 2
    try:
        data_window_end = datetime.date.fromisoformat(data_window_end_str)
    except ValueError:
        print(f"ERROR: bhaga_model > config.data_window_end is not ISO-date: "
              f"{data_window_end_str!r}")
        return 2
    end_of_window_dt = datetime.datetime.combine(
        data_window_end, datetime.time.max, tzinfo=CT,
    )
    window_end_ts_ms = int(end_of_window_dt.timestamp() * 1000)
    print(f"# model data_window_end: {data_window_end} "
          f"(reviews after {end_of_window_dt.isoformat()} held back)")

    # Punches come from the raw sheet (BHAGA's architecture contract: model +
    # downstream code read only from raw sheets, never from local files). The
    # orchestrator's write_raw_sheets step is responsible for keeping it fresh.
    adp_raw_sid = profile["google_sheets"]["bhaga_adp_raw"]["spreadsheet_id"]
    print(f"# loading punches from raw sheet {adp_raw_sid} (BHAGA ADP Raw > punches)")
    punches = read_raw_adp_punches(adp_raw_sid, account=args.store)
    if not punches:
        print("ERROR: BHAGA ADP Raw > punches is empty. Run the orchestrator's "
              "write_raw_sheets step (or backfill_from_downloads.py) first.")
        return 2
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

    # Fetch new messages.
    msgs = fetch_messages(
        REVIEW_CHANNEL_ID, team_id=CLICKUP_TEAM_ID,
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

        # Hard cap at end-of-day CT for data_window_end. Anything posted
        # after that is held back: don't advance the high-water past it, don't
        # parse, don't write. Tomorrow's run (after the window advances) will
        # see this message again.
        if ts_ms > window_end_ts_ms:
            held_back += 1
            continue

        if not _is_review_message(content):
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
            # Unified roster covers everyone the review COULD credit. The
            # allocator decides who actually gets paid based on shoutout vs
            # no-shoutout mode (see allocate_bonus docstring).
            unified_shift = sorted(set(shift_info["shift_members"]) | set(named))
            allocations = allocate_bonus(
                shift_members=unified_shift,
                named=named,
                excluded_permanent=excluded_permanent,
                training_through=training_through,
                shift_date=shift_info["shift_date"],
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
        print("\nDRY RUN — no sheet writes, no Slack.")
        return 0

    # ── Write to Review Raw sheet ──
    # Append parsed reviews to existing rows (idempotent by review_id).
    existing_review_ids = _read_existing_review_ids(raw_sid, token)
    new_review_rows = [
        build_review_row(r) for r in parsed_reviews
        if r["review_id"] not in existing_review_ids
    ]
    skipped = len(parsed_reviews) - len(new_review_rows)
    if skipped:
        print(f"# skipped {skipped} already-seen review(s) by review_id")

    _ensure_review_raw_tabs(raw_sid, token)
    if new_review_rows:
        _append_rows(raw_sid, token, tab="reviews", rows=new_review_rows)
    if unparseable_rows:
        _append_rows(raw_sid, token, tab="unparseable", rows=unparseable_rows)

    # Note: there is intentionally NO config tab on bhaga_review_raw.
    # The incremental high-water mark is derived from the reviews tab itself
    # on the next run; the bonus constants and bonus_started_date live in
    # bhaga_model > config. Architecture rule: one config, on the model.

    # ── Rebuild review_bonus_period on Model sheet ──
    # Pull the FULL reviews tab back, then clamp to data_window_end so the
    # rollup never includes credited-shifts past what the rest of the model
    # has published. Belt-and-braces — the message-loop cap above should
    # already prevent any post-window review from making it into the raw
    # tab, but a manual backfill or human edit could still introduce one.
    all_reviews = _read_all_reviews(
        raw_sid, token,
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

    earnings_xlsx = max(
        DOWNLOADS.glob("Earnings*.xlsx"),
        key=lambda p: p.stat().st_mtime, default=None,
    )
    if earnings_xlsx is None:
        print("# WARN: no Earnings XLSX — skipping review_bonus_period rebuild.")
    else:
        from skills.adp_run_automation import compensation_backend  # noqa: PLC0415
        from agents.bhaga.scripts.update_model_sheet import (  # noqa: PLC0415
            append_open_period, discover_periods,
        )
        earnings = compensation_backend.parse_xlsx(earnings_xlsx, employee_aliases=aliases)
        periods = discover_periods(earnings)
        # Anchor the open-period column to data_window_end (not "today" or
        # latest credited shift) so the rollup's open period ends exactly
        # where the rest of the model ends.
        periods = append_open_period(periods, last_data_date=window_end_iso)
        rollup_rows = build_period_rollup(all_reviews, periods)

        sheet_id = add_sheet_if_missing(model_sid, token, tab_name="review_bonus_period",
                                        column_count=12)
        clear_and_write_tab(model_sid, token, tab_name="review_bonus_period",
                            values=rollup_rows)
        bold_header_row(model_sid, token, sheet_id=sheet_id)
        # Dollar columns are 6, 7, 8 (base, named, total) — 0-indexed.
        format_currency_columns(model_sid, token, sheet_id=sheet_id,
                                column_indices=[6, 7, 8])
        print(f"# review_bonus_period: {len(rollup_rows) - 1} data rows.")

    # ── Slack summary ──
    summary = (
        f"Reviews: +{len(new_review_rows)} (master now {len(all_reviews)}); "
        f"unparseable: +{len(unparseable_rows)}; anomalies: {len(anomalies)}"
    )
    review_anomaly_alert(anomalies)
    success_heartbeat(
        date=datetime.date.today().isoformat(),
        tabs_written=2,
        runtime_s=0.0,
        extra=summary,
    )
    print(f"\nDONE. {summary}")
    return 0


# ── Sheet I/O helpers (Review Raw specific) ──────────────────────────


def _ensure_review_raw_tabs(spreadsheet_id: str, token: str) -> None:
    """Create reviews + unparseable tabs if missing, with headers.

    No config tab — by architecture, all config lives in bhaga_model. The
    incremental high-water mark is derived from the reviews tab on each run.
    """
    sid_reviews = add_sheet_if_missing(spreadsheet_id, token, tab_name="reviews",
                                       column_count=len(REVIEW_HEADER_ROW))
    sid_unparseable = add_sheet_if_missing(spreadsheet_id, token, tab_name="unparseable",
                                           column_count=len(UNPARSEABLE_HEADER_ROW))
    # Seed headers iff tab is empty.
    if not _tab_has_any_data(spreadsheet_id, token, "reviews"):
        clear_and_write_tab(spreadsheet_id, token, tab_name="reviews",
                            values=[REVIEW_HEADER_ROW])
        bold_header_row(spreadsheet_id, token, sheet_id=sid_reviews)
        # Dollar columns: named_credit_each(12), base_credit_each(13), total_bonus(14)
        format_currency_columns(spreadsheet_id, token, sheet_id=sid_reviews,
                                column_indices=[12, 13, 14])
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


def _latest_review_ts_ms(spreadsheet_id: str, token: str) -> Optional[int]:
    """Return the latest post_ts_ms across all rows in the reviews tab.

    This IS the incremental high-water mark — no separate config row needed.
    Reads only column B (post_ts_ct) which is stored as ISO text with a
    leading-quote (see build_review_row). Returns None for an empty sheet.
    """
    rng = urllib.parse.quote("reviews!B2:B100000", safe="!:")
    url = f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{rng}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return None
        raise
    rows = data.get("values", [])
    if not rows:
        return None
    latest: Optional[int] = None
    for r in rows:
        if not r:
            continue
        s = (r[0] or "").strip().lstrip("'")
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
    spreadsheet_id: str,
    token: str,
    *,
    excluded_permanent: set[str],
    training_through: dict[str, datetime.date],
) -> list[dict]:
    """Read the reviews tab back for the rollup pass.

    Rebuilds per-row `allocations` using the CURRENT allocate_bonus policy
    (not whatever was in force when the row was written). That way a policy
    change like the 2026-05-17 shoutout-only switch automatically takes
    effect on the next rollup without needing to rewrite the sheet.
    """
    rng = urllib.parse.quote("reviews!A1:Z100000", safe="!:")
    url = f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{rng}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    rows = data.get("values", [])
    if not rows:
        return []
    header = rows[0]
    out: list[dict] = []
    for r in rows[1:]:
        padded = r + [""] * (len(header) - len(r))
        d = dict(zip(header, padded))
        try:
            d["rating"] = int(d.get("rating") or 0) or None
        except ValueError:
            d["rating"] = None
        d["named"] = _split_names(d.get("named_baristas") or "")
        raw_shift = d.get("shift_date_credited") or ""
        d["shift_date_credited"] = _sheets_serial_to_iso(raw_shift) or (raw_shift or None)
        members = _split_names(d.get("shift_members") or "")
        if d["shift_date_credited"]:
            d["allocations"] = allocate_bonus(
                shift_members=members,
                named=d["named"],
                excluded_permanent=excluded_permanent,
                training_through=training_through,
                shift_date=d["shift_date_credited"],
            )
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
