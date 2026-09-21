"""ADP RUN payroll draft: Start → fill Preview; leave In Progress. Never Approve.

Issue #251. Default is dry-run (print packet, no ADP writes). Live Start requires
``--allow-prod-draft`` and will refuse Approve/Submit/Save locators. The operator
reviews in ADP and submits if it looks right. ``--delete-after`` is cleanup only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, NamedTuple

_APPROVE_DENY = (
    "approve",
    "submit payroll",
    "impound",
    "finish later",
)


HOURS_TOLERANCE_HOURS = 0.5  # 30 minutes
WAGE_TOLERANCE_DOLLARS = 1.0


@dataclass
class PayrollPacketRow:
    employee: str
    labor_type: str
    regular_hours: float
    ot_hours: float
    wage_rate: float | None
    bonus_dollars: float
    misc_reimbursement_dollars: float
    tips_dollars: float = 0.0
    est_wages_dollars: float | None = None
    # Hours to pay at the solo-shift premium rate rather than the base rate
    # (#309). A subset of regular_hours, not an addition to them: it is 0 for
    # anyone ineligible, so it is always exactly what belongs on an ADP rate-2
    # line item.
    solo_premium_hours: float = 0.0


def abort_if_forbidden_label(label: str) -> None:
    """Hard stop if a control looks like Approve/Submit/Finish Later/Save-as-keep."""
    import re

    low = (label or "").strip().lower()
    if "save and continue" in low or "save & continue" in low:
        return
    if "don't save" in low or "dont save" in low:
        return
    for needle in _APPROVE_DENY:
        if needle in low:
            raise RuntimeError(
                f"[adp_payroll_draft] BREADCRUMB forbid_click label={label!r} "
                "never Approve/Submit/Save"
            )
    if re.search(r"\bsave\b", low):
        raise RuntimeError(
            f"[adp_payroll_draft] BREADCRUMB forbid_click label={label!r} "
            "never Approve/Submit/Save"
        )


def solo_rate2_enabled() -> bool:
    """Whether the draft keys the solo premium itself instead of printing it.

    Off by default: the split rewrites Regular hours on a live payroll draft, so
    a selector drift that filled the wrong row would produce a wrong paycheck
    rather than an error. Flip it on per run once the live split has been proven
    against the grid, and see ``docs/FEATURE_FLAGS.md``.
    """
    return os.environ.get("BHAGA_ADP_SOLO_RATE2", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def rate2_split(
    *, adp_regular_hours: float, solo_hours: float
) -> tuple[float, float]:
    """Split ADP's Regular hours into (base hours, premium hours).

    The premium is not extra hours — it is the same hours paid at a higher rate,
    so the two legs must always re-sum to what the timecard imported. The clamp
    is against ADP's Regular figure rather than the model's, because that is the
    number actually in the cell being rewritten; if punches moved after the model
    ran, keying more premium hours than ADP shows would inflate total hours and
    trip the guardrail (or worse, pass and overpay).
    """
    reg = round(max(float(adp_regular_hours or 0), 0.0), 2)
    solo = round(max(float(solo_hours or 0), 0.0), 2)
    if solo <= 0 or reg <= 0:
        return reg, 0.0
    premium = min(solo, reg)
    return round(reg - premium, 2), round(premium, 2)


def _solo_premium_rate(store: str) -> float:
    """The premium hourly rate from ``store_config`` (never hardcoded, pref #29).

    Raises rather than defaulting: picking a rate for a live payroll draft off a
    fallback constant is how a stale policy silently keeps getting paid.
    """
    from core.datastore import fq, read_query

    rows = read_query(
        f"SELECT SAFE_CAST(value AS FLOAT64) AS v FROM {fq('store_config')} "
        f"WHERE store = '{store}' AND key = 'solo_shift_premium_rate_dollars'"
    )
    rate = float((rows or [{}])[0].get("v") or 0)
    if rate <= 0:
        raise RuntimeError(
            "[adp_payroll_draft] BREADCRUMB solo_premium_rate_missing "
            f"store={store} key=solo_shift_premium_rate_dollars"
        )
    return rate


def solo_premium_keying_lines(packet: list["PayrollPacketRow"]) -> list[str]:
    """The rate-2 line items to key into ADP Enter payroll (#309).

    Printed as the handoff when ``BHAGA_ADP_SOLO_RATE2`` is off: for each
    eligible employee, how many of their regular hours move from the base rate to
    the premium rate. Base hours shrink by exactly the premium hours, which is
    what keeps total paid hours equal to the imported timecard.
    """
    lines: list[str] = []
    for row in packet:
        solo = round(float(row.solo_premium_hours or 0), 2)
        if solo <= 0:
            continue
        lines.append(
            f"{row.employee}: rate-1 {round(row.regular_hours - solo, 2)}h, "
            f"rate-2 {solo}h"
        )
    return lines


def _print_solo_premium_keying(packet: list["PayrollPacketRow"]) -> None:
    lines = solo_premium_keying_lines(packet)
    if not lines:
        print("[adp_payroll_draft] solo_premium none eligible this period")
        return
    total = round(sum(float(r.solo_premium_hours or 0) for r in packet), 2)
    print(
        f"[adp_payroll_draft] BREADCRUMB solo_premium_keying n={len(lines)} "
        f"hours={total} (split each employee's Regular hours across two rates)"
    )
    for line in lines:
        print(f"  {line}")


def _solo_premium_hours(row: dict[str, Any], *, regular_hours: float) -> float:
    """Eligible solo hours for one employee-period, clamped to regular hours.

    ``solo_eligible`` is decided upstream in the model, so a false value here
    means no premium at all rather than a zero-hour premium line.

    The clamp matters because solo hours and paid hours come from different
    places: solo minutes are punch-derived, while regular hours come from the
    payroll view after OT is split out. Rounding or a late punch edit could make
    solo exceed regular, and keying more premium hours than the employee is paid
    for would overstate the paycheck.
    """
    if not row.get("solo_eligible"):
        return 0.0
    solo = float(row.get("solo_hours") or 0)
    return round(min(solo, regular_hours), 2)


def packet_from_view_rows(rows: list[dict[str, Any]]) -> list[PayrollPacketRow]:
    out: list[PayrollPacketRow] = []
    for r in rows:
        hours = float(r.get("hours_worked") or 0)
        ot = float(r.get("ot_hours") or 0)
        rate = (
            float(r["wage_rate_dollars"])
            if r.get("wage_rate_dollars") is not None
            else None
        )
        ot_rate = r.get("ot_rate_dollars")
        if r.get("est_gross_pay") is not None:
            est = round(float(r["est_gross_pay"]), 2)
        else:
            est = est_wages_dollars(
                regular_hours=max(hours - ot, 0),
                ot_hours=ot,
                wage_rate=rate,
                ot_rate=(float(ot_rate) if ot_rate is not None else None),
            )
        out.append(
            PayrollPacketRow(
                employee=str(r.get("employee") or ""),
                labor_type=str(r.get("labor_type") or ""),
                regular_hours=round(max(hours - ot, 0), 2),
                ot_hours=round(ot, 2),
                wage_rate=rate,
                bonus_dollars=round(
                    float(r.get("review_bonus") or 0)
                    + float(r.get("recognition_bonus") or 0),
                    2,
                ),
                misc_reimbursement_dollars=round(float(r.get("perks") or 0), 2),
                tips_dollars=round(float(r.get("tips_allocated") or 0), 2),
                est_wages_dollars=est,
                solo_premium_hours=_solo_premium_hours(r, regular_hours=max(hours - ot, 0)),
            )
        )
    return out


def est_wages_dollars(
    *,
    regular_hours: float,
    ot_hours: float,
    wage_rate: float | None,
    ot_rate: float | None = None,
) -> float | None:
    """Match ADP Preview Gross: hours×rate half-up to cents (not IEEE ROUND)."""
    if wage_rate is None:
        return None
    ot_r = wage_rate * 1.5 if ot_rate is None else ot_rate
    total = (
        Decimal(str(regular_hours)) * Decimal(str(wage_rate))
        + Decimal(str(ot_hours)) * Decimal(str(ot_r))
    )
    return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def header_index(headers: list[str], needles: tuple[str, ...]) -> int | None:
    """First header whose lowercase text contains any needle."""
    for i, h in enumerate(headers):
        low = (h or "").lower()
        if any(n in low for n in needles):
            return i
    return None


FILL_COLUMNS = (
    ("tip", ("nqcc", "tips owed", "credit card tip")),
    ("bonus", ("bonus",)),
    ("misc", ("misc reimb", "misc reimburs")),
)


def name_key(name: str) -> str:
    import re

    s = re.sub(r"\s+", " ", (name or "").strip().lower())
    s = re.sub(r"\$[\d.]+\s*/\s*hr.*$", "", s)
    s = re.sub(r"\b[a-z]\.?\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def hours_guardrail_failures(
    ours: dict[str, float],
    adp: dict[str, float],
    *,
    missing_punch_names: list[str] | None = None,
    tolerance: float = HOURS_TOLERANCE_HOURS,
) -> list[str]:
    """Fail if |our−ADP| paid hours > 30 min, we have hours and ADP is blank,
    or ADP has extra paid hours for someone we do not (1:1 roster)."""
    fails: list[str] = []
    for raw in missing_punch_names or []:
        fails.append(f"missing_punch {raw}")
    ours_k = {name_key(k): (k, v) for k, v in ours.items()}
    adp_k = {name_key(k): (k, v) for k, v in adp.items()}
    for key, (label, oh) in ours_k.items():
        if oh <= 0:
            continue
        if key not in adp_k or adp_k[key][1] <= 0:
            fails.append(f"hours_missing_on_adp {label} our={oh}")
            continue
        ah = adp_k[key][1]
        if abs(oh - ah) > tolerance:
            fails.append(f"hours_mismatch {label} our={oh} adp={ah} delta={round(oh-ah, 2)}")
    for key, (label, ah) in adp_k.items():
        if ah <= 0:
            continue
        if key not in ours_k or ours_k[key][1] <= 0:
            fails.append(f"hours_extra_on_adp {label} adp={ah}")
    return fails


def wage_guardrail_failures(
    ours: dict[str, float | None],
    adp: dict[str, float],
    *,
    tolerance: float = WAGE_TOLERANCE_DOLLARS,
) -> list[str]:
    fails: list[str] = []
    adp_k = {name_key(k): (k, v) for k, v in adp.items()}
    for label, ow in ours.items():
        if ow is None:
            continue
        key = name_key(label)
        if key not in adp_k:
            fails.append(f"wages_missing_on_adp {label} our={ow}")
            continue
        ah = adp_k[key][1]
        if abs(ow - ah) > tolerance:
            fails.append(
                f"wages_mismatch {label} our={ow} adp={ah} delta={round(ow-ah, 2)}"
            )
    return fails


def _slack_guardrail(period: str, fails: list[str], *, strict: bool) -> None:
    if not fails:
        return
    verb = "STOPPED (period-end)" if strict else "WARN (mid-period; still Preview, leave draft)"
    body = (
        f"ADP payroll draft guardrail {verb} {period}\n"
        + "\n".join(f"• {f}" for f in fails[:30])
    )
    print(f"[adp_payroll_draft] BREADCRUMB guardrail n={len(fails)} strict={strict}")
    try:
        from agents.bhaga.notify import info_ping

        info_ping(body)
    except Exception as exc:  # noqa: BLE001
        print(f"[adp_payroll_draft] BREADCRUMB slack_failed {exc}")


def run_draft(
    *,
    store: str,
    period_start: str,
    period_end: str,
    dry_run: bool = True,
    allow_prod_draft: bool = False,
    keep_draft: bool = True,
    view_rows: list[dict[str, Any]] | None = None,
    hold_seconds: int = 180,
    allow_start: bool = False,
) -> dict[str, Any]:
    """Build the payroll packet. Live ADP Start is gated; dry-run never Starts.

    Default leaves the In Progress worksheet for the operator to review/submit.
    Never Approve/Save. ``keep_draft=False`` Deletes (cleanup only).
    """
    rows = view_rows if view_rows is not None else _load_view_rows(
        period_start, period_end
    )
    packet = packet_from_view_rows(rows)
    result = {
        "store": store,
        "period_start": period_start,
        "period_end": period_end,
        "dry_run": dry_run,
        "started": False,
        "deleted": False,
        "packet": [asdict(p) for p in packet],
    }
    print("[adp_payroll_draft] packet (compare to /payroll):")
    for row in result["packet"]:
        solo = float(row.get("solo_premium_hours") or 0)
        print(
            f"  {row['employee']}: reg={row['regular_hours']} ot={row['ot_hours']} "
            f"rate={row['wage_rate']} wages={row.get('est_wages_dollars')} "
            f"tips={row.get('tips_dollars')} bonus={row['bonus_dollars']} "
            f"perk={row['misc_reimbursement_dollars']}"
            + (f" solo_premium_hours={solo}" if solo > 0 else "")
        )
    solo_gap = (
        solo_coverage_gap(period_start, period_end)
        if any(float(p.solo_premium_hours or 0) > 0 for p in packet)
        else []
    )
    result["solo_coverage_gap"] = solo_gap
    if solo_gap:
        print(
            "[adp_payroll_draft] BREADCRUMB solo_hours_stale "
            f"missing={','.join(solo_gap)} — premium understated; re-run "
            "materialize_model_bq before keying rate-2"
        )
    _print_solo_premium_keying(packet)
    if dry_run:
        print(
            f"[adp_payroll_draft] dry_run store={store} "
            f"period={period_start}..{period_end} n={len(packet)}"
        )
        return result
    if not allow_prod_draft:
        raise RuntimeError(
            "[adp_payroll_draft] BREADCRUMB refused_start need --allow-prod-draft"
        )
    record_payroll_draft_run(
        store=store,
        period_start=period_start,
        period_end=period_end,
        status="running",
    )
    try:
        live = run_live_preview(
            store=store,
            hold_seconds=hold_seconds,
            allow_start=allow_start,
            packet=packet,
            period_start=period_start,
            period_end=period_end,
            delete_after=not keep_draft,
            solo_gap=solo_gap,
        )
    except Exception as exc:
        record_payroll_draft_run(
            store=store,
            period_start=period_start,
            period_end=period_end,
            status="fail",
            error=repr(exc),
        )
        raise
    result["started"] = live.get("started", False)
    result["deleted"] = live.get("deleted", False)
    result["guardrail_fails"] = live.get("guardrail_fails", [])
    result["screenshots"] = live.get("screenshots", [])
    result["preview_url"] = live.get("preview_url") or ""
    result["preview_hours"] = live.get("preview_hours")
    result["preview_gross"] = live.get("preview_gross")
    result["saved"] = False
    result["approved"] = False
    record_payroll_draft_run(
        store=store,
        period_start=period_start,
        period_end=period_end,
        status="ok",
        preview_hours=result["preview_hours"],
        preview_gross=result["preview_gross"],
    )
    return result


def _adp_headed() -> bool:
    """Visible Chromium only when BHAGA_ADP_HEADED=1. Default is headless."""
    return os.environ.get("BHAGA_ADP_HEADED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def operator_adp_preview_url(page_url: str, payroll_home_url: str = "") -> str:
    """URL the operator opens in their own browser (login as themselves)."""
    for candidate in (page_url, payroll_home_url):
        u = (candidate or "").strip()
        if u.startswith("http") and "adp.com" in u.lower():
            return u
    return "https://runpayroll.adp.com/enrollment.aspx"


def record_payroll_draft_run(
    *,
    store: str,
    period_start: str,
    period_end: str,
    status: str,
    preview_url: str = "",
    preview_hours: float | None = None,
    preview_gross: float | None = None,
    error: str = "",
) -> None:
    """MERGE latest draft status for a period. Best-effort; never raises.

    ``running`` omits totals so a prior Preview snapshot stays until the new
    run finishes. Preview URLs are session hashes and are not stored for UI.
    """
    from datetime import datetime, timezone

    try:
        from core.datastore import load_rows  # noqa: PLC0415

        now = datetime.now(timezone.utc).isoformat()
        row: dict[str, Any] = {
            "store": store,
            "period_start": period_start,
            "period_end": period_end,
            "status": status,
        }
        types = {
            "period_start": "DATE",
            "period_end": "DATE",
            "started_at_utc": "TIMESTAMP",
            "finished_at_utc": "TIMESTAMP",
            "preview_hours": "FLOAT64",
            "preview_gross": "FLOAT64",
        }
        if status == "running":
            row["started_at_utc"] = now
        else:
            row["finished_at_utc"] = now
            row["error"] = error or None
            if preview_hours is not None:
                row["preview_hours"] = float(preview_hours)
            if preview_gross is not None:
                row["preview_gross"] = float(preview_gross)
            if preview_url:
                row["preview_url"] = preview_url
        load_rows(
            "payroll_draft_runs",
            [row],
            merge_keys=["store", "period_start", "period_end"],
            column_bq_types=types,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[adp_payroll_draft] BREADCRUMB record_run_failed {exc}")


def _load_view_rows(period_start: str, period_end: str) -> list[dict[str, Any]]:
    from core.datastore import fq, read_query

    if not _iso(period_start) or not _iso(period_end):
        raise ValueError("period_start/end must be YYYY-MM-DD")
    sql = (
        f"SELECT * FROM {fq('vw_model_payroll_period')} "
        f"WHERE period_start = DATE '{period_start}' "
        f"AND period_end = DATE '{period_end}' "
        "ORDER BY employee"
    )
    rows = read_query(sql)
    if not rows:
        # Open-period view often ends before the ADP biweek Sunday.
        rows = read_query(
            f"SELECT * FROM {fq('vw_model_payroll_period')} "
            f"WHERE period_start = DATE '{period_start}' "
            "ORDER BY employee"
        )
    return _merge_solo_hours(rows, period_start)


def _merge_solo_hours(
    rows: list[dict[str, Any]], period_start: str
) -> list[dict[str, Any]]:
    """Attach solo-shift hours (#309) to payroll rows, keyed on canonical name.

    Joined in Python rather than folded into ``vw_model_payroll_period`` so the
    payroll view keeps one owner. A missing or empty solo view leaves every row
    untouched: no solo data must never block a payroll draft, since the base
    hours are correct without it.
    """
    from core.datastore import fq, read_query

    try:
        solo = read_query(
            f"SELECT employee, solo_hours, eligible FROM {fq('vw_solo_hours_period')} "
            f"WHERE period_start = DATE '{period_start}'"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[adp_payroll_draft] BREADCRUMB solo_hours_unavailable {exc}")
        return rows

    by_name = {name_key(str(s.get("employee") or "")): s for s in solo or []}
    for row in rows:
        match = by_name.get(name_key(str(row.get("employee") or "")))
        if not match:
            continue
        row["solo_hours"] = match.get("solo_hours")
        row["solo_eligible"] = match.get("eligible")
    return rows


def solo_coverage_gap(period_start: str, period_end: str) -> list[str]:
    """Period dates whose solo hours do not reflect the current punches (#309).

    Solo hours are a separate materialization from the punches they derive from,
    so the two drift whenever that step is skipped, a nightly fails after ingest,
    or punches are edited afterwards. A drifted premium does not look like an
    error — it simply comes out smaller, which is indistinguishable from a quiet
    fortnight.

    Two ways to drift, and both matter:

    - **Missing** — the date has punches and no solo row at all. Caught live
      2026-09-20, understating the closing cycle as 12.24h/$12.24 against an
      actual 20.16h, one employee ~$8 short.
    - **Disagreeing** — the date's solo rows account for a different number of
      worked minutes than its punches do. This is the operator-edit case: fixing
      a forgotten punch-out in ADP and pressing **Sync clocked hours** rewrites
      `adp_punches` but never runs the materialize, and a restored coworker punch
      is exactly what flips minutes from solo to team.

    Compared on minutes rather than on timestamps: ``scraped_at_utc`` is only
    stamped by the Sync-clocked-hours path, so it is NULL for everything the
    nightly ingested and a ``built_at < scraped_at`` test silently passes on the
    majority of dates. Minutes are exact — every date in the 09-07..09-20 cycle
    reconciles to 0 — because invariant 11 makes ``solo + team`` the same quantity
    the punches describe.

    In-scope only for BQ: this proves solo hours match the punches *in BQ*. It
    cannot know whether BQ matches ADP — that is what the Timecard scrape is for.
    """
    from core.datastore import fq, read_query

    try:
        rows = read_query(
            "WITH p AS ("
            "  SELECT date, ROUND(SUM(total_hours) * 60) AS punch_min"
            f"  FROM {fq('adp_punches')}"
            f"  WHERE date BETWEEN DATE '{period_start}' AND DATE '{period_end}'"
            "   GROUP BY date"
            "), s AS ("
            "  SELECT date, SUM(total_minutes) AS solo_min"
            f"  FROM {fq('model_solo_hours_daily')} GROUP BY date"
            ") "
            "SELECT FORMAT_DATE('%Y-%m-%d', p.date) AS d "
            "FROM p LEFT JOIN s USING (date) "
            "WHERE s.date IS NULL OR ABS(s.solo_min - p.punch_min) > 1 "
            "ORDER BY 1"
        )
    except Exception as exc:  # noqa: BLE001
        # An unreadable check must not be silently treated as "no gap": the whole
        # point is to refuse to key money we cannot vouch for.
        print(f"[adp_payroll_draft] BREADCRUMB solo_coverage_unknown {exc}")
        return ["unknown"]
    return [str(r.get("d")) for r in rows or []]


def _iso(s: str) -> bool:
    import re

    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s))


def _shot_dir():
    import pathlib

    d = pathlib.Path.home() / ".bhaga" / "state" / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def screenshot_preview(page, label: str) -> str:
    import datetime

    path = _shot_dir() / f"adp-payroll-{label}-{datetime.datetime.now():%Y%m%d-%H%M%S}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"[adp_payroll_draft] screenshot {path}")
    return str(path)


def _visible_action_labels(page) -> list[str]:
    labels: list[str] = []
    for loc in (
        page.get_by_role("button"),
        page.get_by_role("link"),
        page.locator("[data-test-id$='-btn']"),
    ):
        try:
            n = loc.count()
        except Exception:  # noqa: BLE001
            continue
        for i in range(min(n, 80)):
            try:
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                text = (el.inner_text() or "").strip().replace("\n", " ")
                test_id = el.get_attribute("data-test-id") or ""
                if text or test_id:
                    labels.append(f"{text or '(no text)'} [{test_id}]")
            except Exception:  # noqa: BLE001
                continue
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for x in labels:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _open_payroll_home(page) -> None:
    import re

    from skills.adp_run_automation.runner import POST_LOGIN_URL_RE

    page.locator('[data-test-id="Payroll-btn"]').first.click(timeout=15_000)
    page.wait_for_timeout(1_500)
    print(f"[adp_payroll_draft] payroll_home url={page.url}")
    home_ready = page.locator(
        "[data-test-id='PAYRUN_REGULAR-tile'], [data-test-id^='active-payroll-']"
    ).first
    try:
        home_ready.wait_for(state="visible", timeout=20_000)
    except Exception:  # noqa: BLE001
        print("[adp_payroll_draft] WARN payroll home tiles not visible yet")
    if not POST_LOGIN_URL_RE.search(page.url):
        print("[adp_payroll_draft] WARN still not on v2 dashboard after Payroll click")
    for lab in _visible_action_labels(page):
        print(f"[adp_payroll_draft] control {lab!r}")


_PAYROLL_HOME_DUMP_JS = """() => {
  const items = [];
  const seen = new Set();
  const els = document.querySelectorAll(
    "a, button, [role='link'], [role='button'], [data-test-id]"
  );
  for (const el of els) {
    const id = el.getAttribute("data-test-id") || "";
    const href = el.getAttribute("href") || "";
    const text = (el.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 240);
    const blob = (id + " " + href + " " + text).toLowerCase();
    if (!/(pay|payroll|history|complete|check|regular)/.test(blob)) continue;
    const key = [id, href, text].join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    items.push({ tag: el.tagName, id, href, text });
  }
  return { url: location.href, n: items.length, items: items.slice(0, 150) };
}"""


def collect_payroll_home_dump(page) -> dict[str, Any]:
    """Read-only snapshot of Payroll Home links/tiles (no Start/Approve)."""
    return page.evaluate(_PAYROLL_HOME_DUMP_JS)


def dump_payroll_home_history(*, store: str = "palmetto") -> dict[str, Any]:
    """Login → Payroll Home → print tiles/links. Never Start or Approve."""
    from skills.adp_run_automation.runner import adp_session  # noqa: PLC0415

    headed = _adp_headed()
    with adp_session(store=store, headed=headed, slow_mo_ms=0) as (_ctx, page):
        _open_payroll_home(page)
        page.wait_for_timeout(2_000)
        dump = collect_payroll_home_dump(page)
        print(f"[adp_payroll_draft] BREADCRUMB list_history url={dump.get('url')}")
        import json

        print(json.dumps(dump, indent=2)[:24_000])
        shot = screenshot_preview(page, "payroll-home-history")
        dump["screenshot"] = shot

        details = page.locator(
            "[data-test-id='latest-payroll-view-payrolls-details']"
        ).first
        try:
            details.wait_for(state="visible", timeout=8_000)
            abort_if_forbidden_label(details.inner_text() or "Payroll details")
            print("[adp_payroll_draft] BREADCRUMB click latest-payroll-view-payrolls-details")
            details.click()
            page.wait_for_timeout(3_500)
            dump["details_url"] = page.url
            dump["details"] = collect_payroll_home_dump(page)
            print(f"[adp_payroll_draft] BREADCRUMB details_url={page.url}")
            print(json.dumps(dump["details"], indent=2)[:24_000])
            dump["details_screenshot"] = screenshot_preview(page, "payroll-details")
        except Exception as exc:  # noqa: BLE001
            print(f"[adp_payroll_draft] BREADCRUMB details_click_failed {exc}")
        return dump


def _wait_wizard_ready(page) -> None:
    """Run payroll lands on a spinner, timecard, or in-progress import modal."""
    import re

    page.wait_for_timeout(1_000)
    for _ in range(45):
        try:
            if page.get_by_role("button", name="Finish later").first.is_visible():
                print(f"[adp_payroll_draft] wizard_ready url={page.url}")
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            if page.get_by_role(
                "button", name=re.compile(r"Import latest timecards", re.I)
            ).first.is_visible():
                print("[adp_payroll_draft] wizard_ready in-progress-import-modal")
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            if page.get_by_role("button", name="Preview payroll").first.is_visible():
                print("[adp_payroll_draft] wizard_ready enter-payroll")
                return
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(2_000)
    raise TimeoutError("payroll wizard not ready")


def _click_visible_text(page, text: str) -> bool:
    loc = page.get_by_text(text, exact=True)
    try:
        n = loc.count()
    except Exception:  # noqa: BLE001
        return False
    for i in range(n):
        el = loc.nth(i)
        try:
            if el.is_visible():
                abort_if_forbidden_label(text)
                el.click()
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _click_start_if_present(page) -> bool:
    """Click Payroll Home 'Run payroll' or Resume (ADP RUN v2)."""
    import re

    resume = page.get_by_role("button", name=re.compile(r"Resume", re.I)).first
    try:
        resume.wait_for(state="visible", timeout=3_000)
        abort_if_forbidden_label(resume.inner_text() or "Resume")
        print("[adp_payroll_draft] BREADCRUMB clicking Resume payroll (no Save/Approve)")
        resume.click()
        return True
    except Exception:
        pass

    active = page.locator("[data-test-id^='active-payroll-']").first
    try:
        active.wait_for(state="visible", timeout=4_000)
        abort_if_forbidden_label(active.inner_text() or "active payroll")
        print("[adp_payroll_draft] BREADCRUMB clicking active-payroll row")
        active.click()
        return True
    except Exception:
        pass

    tile = page.locator("[data-test-id='PAYRUN_REGULAR-tile']").first
    try:
        tile.wait_for(state="visible", timeout=5_000)
        abort_if_forbidden_label("Run payroll")
        print("[adp_payroll_draft] BREADCRUMB clicking PAYRUN_REGULAR-tile")
        tile.click()
        return True
    except Exception:
        pass

    run = page.locator("[data-test-id='run-payroll-btn']").first
    try:
        run.wait_for(state="visible", timeout=8_000)
    except Exception:
        run = page.get_by_role("button", name=re.compile(r"Run payroll", re.I)).first
        try:
            run.wait_for(state="visible", timeout=5_000)
        except Exception:
            print("[adp_payroll_draft] no Run payroll / Resume on Payroll Home")
            return False
    abort_if_forbidden_label(run.inner_text() or "Run payroll")
    print("[adp_payroll_draft] BREADCRUMB clicking Run payroll (preview-only; no Save/Approve)")
    run.click()
    return True


def _attribute_grid_rows(raw: list[dict]) -> list[dict]:
    """Name every grid row, including an employee's unnamed continuation lines.

    ADP prints the employee name on their *first* line item only, so a second
    rate line (#309) comes back with an empty pinned-left cell. Skipping nameless
    rows — which this reader used to do — made the row we had just added
    invisible, and the "the index that is not the base index" fallback then
    resolved to the employee's own renumbered original row. Live 2026-09-21: the
    premium landed on the original row and the reduced base hours landed on a
    different employee's row, twice reported as `no_new_row` / `total_changed`.

    Attribution is carry-forward in grid order, which is exactly how the grid
    reads on screen: a nameless row belongs to the nearest named row above it.
    Rows before any named row are dropped — they are headers or filler, not pay.
    """
    out: list[dict] = []
    current = ""
    for row in sorted(raw, key=lambda r: int(r.get("row_index") or -1)):
        name = (row.get("name_text") or "").strip()
        if "," in name:
            current = name.split("\n")[0].strip()
            continuation = False
        elif current and row.get("has_body"):
            continuation = True
        else:
            continue
        out.append({
            "employee": current,
            "row_index": str(row.get("row_index")),
            "rate": float(row.get("rate") or 0),
            "reg": float(row.get("reg") or 0),
            "pers": float(row.get("pers") or 0),
            "hol": float(row.get("hol") or 0),
            "ot": float(row.get("ot") or 0),
            "continuation": continuation,
        })
    return out


def _ag_grid_rows(page) -> list[dict]:
    """One entry per visible Enter-payroll grid row, in grid order.

    Deliberately does not collapse by employee: solo-shift pay (#309) gives one
    employee two rows on the same check, and the caller needs each row's
    ``row_index`` to address it.
    """
    raw = page.evaluate(
        """() => {
          const num = (t) => {
            const s = (t || '').replace(/[$,]/g, '').trim();
            const n = parseFloat(s);
            return Number.isFinite(n) ? n : 0;
          };
          const out = [];
          for (const row of document.querySelectorAll(
            '.ag-pinned-left-cols-container [role="row"]'
          )) {
            const idx = row.getAttribute('row-index');
            if (idx === null) continue;
            const text = (row.innerText || '').replace(/\\s+/g, ' ').trim()
              .replace(/\\s*\\$[\\d.]+\\s*\\/\\s*hr.*$/i, '')
              .trim();
            const body = document.querySelector(
              '.ag-center-cols-container [role="row"][row-index="' + idx + '"]'
            );
            const cell = (id) => {
              const el = body && body.querySelector('[col-id="' + id + '"]');
              return el ? num(el.innerText) : 0;
            };
            // A continuation row carries no name, so its rate lives in the body's
            // rate cell rather than in the pinned text.
            const rateText = (body
              && body.querySelector('[col-id="rate_employee"]')
              || {}).innerText || row.innerText || '';
            const rateM = (rateText || '').match(/\\$?([0-9]+\\.[0-9]+)/);
            out.push({
              name_text: text,
              row_index: idx,
              has_body: Boolean(body),
              rate: rateM ? parseFloat(rateM[1]) : 0,
              reg: cell('REGH'),
              pers: cell('PERSH'),
              hol: cell('HOLH'),
              ot: cell('OVTH') + cell('NQOVTH'),
            });
          }
          return out;
        }"""
    )
    return _attribute_grid_rows(raw or [])


def _aggregate_grid_rows(rows: list[dict]) -> dict[str, dict]:
    """Fold grid rows into one paid-hours record per employee.

    An employee owns more than one row once they are paid at two rates: solo
    hours at $16.25 sit on their own line item next to team hours at $15.25.
    Keying by name and assigning kept only the last row, which understated
    hours for the guardrail comparison and hid stale rows from the zeroing pass.

    ``rate`` reports the lowest rate seen so it stays the base rate rather than
    drifting up to the premium; ``rows`` keeps every line item for diagnosis and
    for addressing cells by ``row_index``.
    """
    out: dict[str, dict] = {}
    for row in rows:
        employee = row.get("employee") or ""
        if not employee:
            continue
        rec = out.setdefault(
            employee,
            {"hours": 0.0, "reg": 0.0, "pers": 0.0, "hol": 0.0, "ot": 0.0,
             "rate": 0.0, "rows": []},
        )
        for key in ("reg", "pers", "hol", "ot"):
            rec[key] = round(rec[key] + float(row.get(key) or 0), 2)
        rec["hours"] = round(rec["reg"] + rec["pers"] + rec["hol"] + rec["ot"], 2)
        rate = float(row.get("rate") or 0)
        if rate > 0 and (rec["rate"] <= 0 or rate < rec["rate"]):
            rec["rate"] = rate
        rec["rows"].append(row)
    return out


def _ag_enter_page_hours(page) -> dict[str, dict]:
    """Paid hours on Enter payroll AG Grid: Regular + Personal + Holiday + OT."""
    return _aggregate_grid_rows(_ag_grid_rows(page))


def _grid_rewind(page) -> None:
    """Page the Enter-payroll grid back to the first page."""
    prev = page.locator("[data-test-id='pagination-chevron-left']").first
    for _ in range(6):
        try:
            if prev.is_visible() and prev.is_enabled():
                prev.click()
                page.wait_for_timeout(400)
            else:
                return
        except Exception:  # noqa: BLE001
            return


def _paginate_timecard_hours(page) -> dict[str, float]:
    # Collect by (page ordinal, row-index). AG Grid restarts `row-index` at 0 on
    # every page, so row-index alone is NOT a stable key across pages: page 2's
    # rows silently overwrite page 1's first N. Caught 2026-09-21 on a 15-person
    # roster at 10/page — the 4 rows of page 2 displaced Alvarez/Browning/Garcia/
    # Guerrero, and the 3 of them with hours read as absent, failing the hours
    # guardrail with n=3 and blocking the rate-2 split. ADP itself was correct:
    # every one of the three reconciled exactly at Preview.
    #
    # The page ordinal keeps the two properties this key needs: re-reading the
    # same page cannot double-count (same keys overwrite), and a two-rate
    # employee's separate line items stay separate.
    # Paging is what makes the ordinal safe, so a page that does not actually
    # advance must stop the walk: otherwise the same rows are banked again under
    # a fresh ordinal and every hour on them counts twice. Overstated hours would
    # pass nothing downstream but they would fail the guardrail for the wrong
    # reason, hiding a selector drift behind a plausible-looking mismatch.
    # Rewind first, not only afterwards: this is called again after the rate-2
    # split, which leaves the grid on the last page. Starting there banked only
    # that page and reported 3 of 15 employees as the whole roster (live
    # 2026-09-21), turning a clean grid into 10 guardrail failures.
    _grid_rewind(page)
    seen: dict[tuple[int, str], dict] = {}
    last_sig: tuple | None = None
    for page_no in range(6):
        rows = _ag_grid_rows(page)
        sig = tuple(
            (r.get("employee"), str(r.get("row_index")), r.get("reg")) for r in rows
        )
        if sig == last_sig:
            print(
                "[adp_payroll_draft] BREADCRUMB grid_page_did_not_advance "
                f"page={page_no} rows={len(rows)} (stopping; hours read so far kept)"
            )
            break
        last_sig = sig
        for row in rows:
            seen[(page_no, str(row.get("row_index")))] = row
        nxt = page.locator("[data-test-id='pagination-chevron-right']").first
        try:
            if nxt.is_visible() and nxt.is_enabled():
                nxt.click()
                page.wait_for_timeout(1_000)
                continue
        except Exception:  # noqa: BLE001
            break
        break
    _grid_rewind(page)
    detail = _aggregate_grid_rows(list(seen.values()))
    print(f"[adp_payroll_draft] ag_enter_hours {detail}")
    return {name: float(rec.get("hours") or 0) for name, rec in detail.items()}


def _open_row_action_menu(page, *, row_index: str) -> bool:
    """Open one Enter-payroll row's "Employee Options" overflow menu.

    The trigger is an ``sdf-action-menu`` custom element, not a ``button`` or
    ``sdf-button`` — querying those found nothing and the first live proof failed
    ``no_row_menu`` on all six employees (2026-09-16). It lives in the pinned-left
    Name cell alongside the employee-warning icon.
    """
    return bool(
        page.evaluate(
            """(idx) => {
              const row = document.querySelector(
                '.ag-pinned-left-cols-container [role="row"][row-index="' + idx + '"]'
              );
              if (!row) return false;
              const menu = row.querySelector(
                'sdf-action-menu[aria-label="Employee Options"], '
                + 'sdf-action-menu.ee-menu-wrapper, sdf-action-menu'
              );
              if (!menu) return false;
              menu.scrollIntoView({ block: 'center' });
              menu.click();
              return true;
            }""",
            row_index,
        )
    )


# A closed row menu still has its items in the DOM, so "exists" is never enough
# to identify the menu we just opened — only "rendered" is.
_JS_VISIBLE = """
  const visible = (el) => {
    if (!el || !el.getClientRects || !el.getClientRects().length) return false;
    if (el.getAttribute && el.getAttribute('aria-hidden') === 'true') return false;
    for (let n = el; n; n = n.parentElement) {
      if (n.hasAttribute && n.hasAttribute('hidden')) return false;
    }
    return true;
  };
"""


def _click_menu_item(
    page, label: str, *, test_id: str = "", row_index: str = ""
) -> bool:
    """Click a row-menu item belonging to ``row_index``, not merely one that matches.

    ADP renders an action menu for *every* grid row, so a document-wide
    ``querySelector('[data-test-id="optionsAddRowButton"]')`` returns the first
    row's item regardless of which menu is open. That is what it used to do, and
    it is why "Add row" always landed on the first row of the page — live
    2026-09-21 the empty rows piled up on Alvarez (page 1, index 0) and Krause
    (page 2, index 0) while the employees we targeted never got one.

    So it walks a ladder, most specific first. Inside the opened row's subtree,
    a match is correct by construction. Away from it, the element must be
    *rendered*: a closed menu's items are present but have no client rects, which
    is what tells them apart from the menu just opened. A miss logs what it saw,
    so diagnosing the next drift costs no extra ADP login.
    """
    abort_if_forbidden_label(label)
    out = page.evaluate(
        """({ label, testId, rowIndex }) => {
              %s
              const want = label.toLowerCase();
              const sel = 'sdf-menu-item, [role="menuitem"], [role="option"], '
                + 'li, button, sdf-button';
              const hit = (el) => {
                if (testId && el.getAttribute
                    && el.getAttribute('data-test-id') === testId) return true;
                const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                return t.toLowerCase() === want;
              };
              const find = (root) => {
                const pool = [...root.querySelectorAll(sel)];
                if (testId) {
                  for (const el of root.querySelectorAll(
                    '[data-test-id="' + testId + '"]'
                  )) pool.unshift(el);
                }
                return pool.filter(hit);
              };
              const rowOf = (el) => {
                const r = el.closest('[role="row"]');
                return r ? r.getAttribute('row-index') : null;
              };
              const row = rowIndex === '' ? null : document.querySelector(
                '.ag-pinned-left-cols-container [role="row"][row-index="'
                + rowIndex + '"]'
              );
              // Strategy ladder, most specific first. Scoping to the row is
              // already proof of correctness, so visibility is not required
              // there; away from the row it is the only thing that tells the
              // open menu apart from the 14 closed ones.
              const ladder = [];
              if (row) {
                ladder.push(['row_visible', find(row).filter(visible)]);
                ladder.push(['row_any', find(row)]);
              }
              const all = find(document);
              if (rowIndex !== '') {
                ladder.push([
                  'doc_by_row', all.filter((el) => rowOf(el) === rowIndex),
                ]);
              }
              ladder.push(['doc_visible', all.filter(visible)]);
              for (const [how, found] of ladder) {
                if (found.length) {
                  found[0].scrollIntoView({ block: 'center' });
                  found[0].click();
                  return { clicked: true, how, n: found.length };
                }
              }
              // Nothing clicked: describe what is there so the next run does not
              // need another login to find out.
              return {
                clicked: false,
                how: 'none',
                candidates: all.slice(0, 6).map((el) => ({
                  tag: el.tagName,
                  testId: el.getAttribute && el.getAttribute('data-test-id'),
                  row: rowOf(el),
                  rects: el.getClientRects().length,
                  ariaHidden: el.getAttribute && el.getAttribute('aria-hidden'),
                  display: getComputedStyle(el).display,
                  text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 40),
                })),
                rowFound: Boolean(row),
                openMenus: [...document.querySelectorAll(
                  'sdf-menu, [role="menu"], sdf-action-menu'
                )].filter(visible).length,
              };
            }""" % _JS_VISIBLE,
        {"label": label, "testId": test_id, "rowIndex": row_index},
    )
    if not (out or {}).get("clicked"):
        print(f"[adp_payroll_draft] menu_item_miss {label!r} row={row_index} {out}")
        return False
    if out.get("how") != "row_visible":
        print(
            f"[adp_payroll_draft] menu_item_hit {label!r} row={row_index} "
            f"via={out.get('how')} n={out.get('n')}"
        )
    return True


def _select_available_rate(page, *, row_index: str, rate_dollars: float) -> bool:
    """Pick a rate on one grid row via its "Select Available Rates" control.

    The collapsed cell shows only the primary rate even when a second exists on
    the employee's profile (verified live 2026-09-16: Willingham's cell read
    ``$15.2500 / hr`` twice while the opened selector listed both $15.25 and
    $16.25), so the rate must be chosen through the selector, never read off the
    cell. Matching is on the dollars-and-cents prefix because ADP renders four
    decimals (``$16.2500 / hr``).
    """
    opened = page.evaluate(
        """(idx) => {
          const row = document.querySelector(
            '.ag-pinned-left-cols-container [role="row"][row-index="' + idx + '"]'
          );
          const cell = row && row.querySelector('[col-id="availableRates"]');
          const btn = cell && cell.querySelector('sdf-button, button');
          if (!btn) return false;
          btn.scrollIntoView({ block: 'center' });
          btn.click();
          return true;
        }""",
        row_index,
    )
    if not opened:
        print(f"[adp_payroll_draft] no_rate_selector row={row_index}")
        return False
    page.wait_for_timeout(600)
    want = f"${rate_dollars:.2f}"
    # Only rendered options: every row's collapsed selector has its rates in the
    # DOM, so an unguarded document scan can pick another employee's rate.
    picked = page.evaluate(
        """({ want }) => {
          %s
          const nodes = [...document.querySelectorAll(
            'sdf-menu-item, [role="menuitem"], [role="option"], li, button, div'
          )];
          for (const el of nodes.reverse()) {
            const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!/\\/\\s*hr/i.test(t)) continue;
            if (!visible(el)) continue;
            if (t.split('/')[0].trim().startsWith(want)) {
              el.scrollIntoView({ block: 'center' });
              el.click();
              return t;
            }
          }
          return '';
        }""" % _JS_VISIBLE,
        {"want": want},
    )
    if not picked:
        # Which rates the selector actually offers. A rate that is not on the
        # employee's ADP profile can never be picked here, so this distinguishes
        # "our selector drifted" from "this employee has no second rate yet".
        offered = page.evaluate(
            """() => {
              %s
              const out = [];
              for (const el of document.querySelectorAll(
                'sdf-menu-item, [role="menuitem"], [role="option"], li, button, div'
              )) {
                const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!/\\/\\s*hr/i.test(t)) continue;
                if (t.length > 40) continue;
                out.push({ text: t, rects: el.getClientRects().length,
                           shown: visible(el) });
              }
              return out.slice(0, 10);
            }""" % _JS_VISIBLE
        )
        print(
            f"[adp_payroll_draft] rate_pick_miss row={row_index} want={want} "
            f"offered={offered}",
            flush=True,
        )
    print(
        f"[adp_payroll_draft] rate_pick row={row_index} want={want} got={picked!r}",
        flush=True,
    )
    return bool(picked)


def unrepairable_hours_fails(
    ours: dict[str, float],
    adp: dict[str, float],
    solo: dict[str, float],
    *,
    tolerance: float = HOURS_TOLERANCE_HOURS,
) -> list[str]:
    """Guardrail failures that a rate-2 repair would *not* explain.

    The hours guardrail blocks the split, and a half-applied split breaks the
    hours guardrail — so a failed earlier run leaves a state that can never be
    fixed by rerunning. That deadlock is why this exists.

    A half-applied split has one signature: ADP shows exactly ``solo_hours`` more
    than the console for an employee who is owed a premium of ``solo_hours``,
    because the premium row was added without reducing the base row. Anything
    else is a real disagreement and must keep blocking.

    Returns the failures that remain unexplained; empty means the repair is safe
    to run, and the hours are re-checked afterwards regardless.
    """
    solo_k = {name_key(k): v for k, v in solo.items()}
    ours_k = {name_key(k): v for k, v in ours.items()}
    remaining: list[str] = []
    for fail in hours_guardrail_failures(ours, adp):
        if not fail.startswith("hours_mismatch "):
            remaining.append(fail)
            continue
        label = fail.split("hours_mismatch ", 1)[1].split(" our=")[0]
        key = name_key(label)
        premium = float(solo_k.get(key) or 0)
        oh = float(ours_k.get(key) or 0)
        ah = next(
            (v for k, v in adp.items() if name_key(k) == key), None
        )
        if premium <= 0 or ah is None:
            remaining.append(fail)
            continue
        if abs((ah - oh) - premium) > tolerance:
            remaining.append(fail)
    return remaining


class Rate2Plan(NamedTuple):
    """What to do about one employee's existing grid rows.

    ``verdict`` is one of ``split`` (go ahead), ``repair`` (a half-applied split
    to finish), ``already_split`` (leave it), ``suspect`` (report, touch nothing).
    """

    verdict: str
    base_index: str = ""
    base_hours: float = 0.0
    #: An existing empty rate line to reuse rather than adding another.
    reuse_index: str | None = None
    why: str = ""


def _row_is_empty(row: dict) -> bool:
    return all(
        abs(float(row.get(k) or 0)) < 0.005 for k in ("reg", "pers", "hol", "ot")
    )


def classify_rate2_state(
    lines: list[dict], *, solo_hours: float, want_total: float | None
) -> Rate2Plan:
    """Decide whether an employee still needs the premium split, from their rows.

    Three things can be true of a live draft, and conflating them is how money
    goes wrong:

    * one funded row carrying all the hours — split it;
    * two funded rows that already reproduce the split — leave it alone, since
      re-splitting pays the uplift twice;
    * anything else — report it. A failed run can leave rows that are present but
      wrong, and "more than one row" is not evidence of a *correct* one.

    Empty rate lines are debris from a failed attempt, not pay. They are reused
    rather than deleted or added to, so repeated attempts cannot make the mess
    grow and no destructive delete path is needed.
    """
    funded = [r for r in lines if not _row_is_empty(r)]
    empty = [r for r in lines if _row_is_empty(r)]
    total = round(sum(float(r.get("reg") or 0) for r in funded), 2)

    def reg(row) -> float:
        return float(row.get("reg") or 0)

    if want_total is None:
        return Rate2Plan("suspect", why=f"no_expected_total lines={len(lines)}")

    if len(funded) == 2:
        solo_row = next(
            (r for r in funded if abs(reg(r) - solo_hours) < 0.011), None
        )
        if solo_row is None:
            return Rate2Plan(
                "suspect",
                why=f"two_funded_rows_without_solo_line solo={solo_hours} "
                    f"total={total} want_total={want_total}",
            )
        base_row = next(r for r in funded if r is not solo_row)
        if abs(total - want_total) < 0.011:
            return Rate2Plan("already_split")
        if abs(reg(base_row) - want_total) < 0.011:
            # Half-applied: the premium row was added but the base row was never
            # reduced, so the employee gained `solo_hours` instead of having them
            # repriced. Live 2026-09-21 this inflated Huynh by 0.97h and Perales
            # by 1.98h. Repairable by writing both rows: the premium row already
            # holds the right hours at the right rate, the base one does not.
            return Rate2Plan(
                "repair",
                base_index=str(base_row.get("row_index")),
                base_hours=want_total,
                reuse_index=str(solo_row.get("row_index")),
                why=f"base_not_reduced base={reg(base_row)} total={total}",
            )
        return Rate2Plan(
            "suspect",
            why=f"total={total} want_total={want_total} lines={len(lines)}",
        )

    if abs(total - want_total) >= 0.011:
        return Rate2Plan(
            "suspect",
            why=f"total={total} want_total={want_total} lines={len(lines)}",
        )
    if len(funded) != 1:
        return Rate2Plan(
            "suspect", why=f"funded_rows={len(funded)} lines={len(lines)}"
        )
    base = funded[0]
    return Rate2Plan(
        "split",
        base_index=str(base.get("row_index")),
        base_hours=reg(base),
        reuse_index=str(empty[0].get("row_index")) if empty else None,
    )


def _apply_solo_rate2(page, packet: list[PayrollPacketRow], *, premium_rate: float) -> dict:
    """Key each eligible employee's premium hours onto a second rate line.

    Sequence per employee, from the live grid probe: the row's overflow menu ->
    "Add row" creates a second line item, its "Select Available Rates" control
    picks the premium rate, the premium hours go in that row's Regular cell, and
    the original row's Regular cell is reduced by the same amount.

    Reducing the original row last matters: if the run dies mid-employee, total
    paid hours are too high and the guardrail catches it, whereas reducing first
    would leave hours missing and a short paycheck that still looks self
    consistent.

    Every employee is verified by re-reading the grid, and any mismatch is
    reported rather than retried — a retry on a half-applied split would double
    the premium line.
    """
    applied: list[str] = []
    failed: list[str] = []
    wanted = {
        name_key(r.employee): round(float(r.solo_premium_hours or 0), 2)
        for r in packet
        if float(r.solo_premium_hours or 0) > 0
    }
    if not wanted:
        return {"applied": [], "failed": [], "premium_hours": 0.0}

    # Regular hours we expect each employee's rate lines to add back up to, used
    # to audit a split that already exists rather than assuming it is ours.
    expected_total = {
        name_key(r.employee): round(float(r.regular_hours or 0), 2)
        for r in packet
    }

    done: set[str] = set()
    for _ in range(6):
        # One employee per *fresh* grid read. "Add row" renumbers every row below
        # the insert, so a snapshot taken before the first insert addresses the
        # wrong rows for everyone after it. Live 2026-09-21: Alvarez's insert
        # shifted the grid, Garcia's stale base index then pointed at another
        # employee's row, and the run wrote 41.65 h somewhere it did not belong.
        # Re-reading before every mutation is the only index that stays true.
        while True:
            _dismiss_adp_error_dialog(page)
            page_map = _ag_enter_page_hours(page)
            todo = [
                (n, r)
                for n, r in page_map.items()
                if name_key(n) in wanted and name_key(n) not in done
            ]
            if not todo:
                break
            name, rec = todo[0]
            key = name_key(name)
            done.add(key)  # one attempt each, success or not: never retry a
            # half-applied split, which would double the premium line.
            solo = wanted[key]
            lines = rec.get("rows") or []
            want_total = expected_total.get(key)
            plan = classify_rate2_state(
                lines, solo_hours=solo, want_total=want_total
            )
            if plan.verdict == "already_split":
                print(
                    f"[adp_payroll_draft] BREADCRUMB solo_rate2_skip_existing "
                    f"{name!r} line_items={len(lines)} solo={solo}"
                )
                applied.append(name)
                continue
            if plan.verdict == "repair":
                print(
                    f"[adp_payroll_draft] BREADCRUMB solo_rate2_repairing "
                    f"{name!r} {plan.why}"
                )
            if plan.verdict == "suspect":
                failed.append(f"{name}: unexpected_existing_split {plan.why}")
                print(
                    "[adp_payroll_draft] BREADCRUMB solo_rate2_failed "
                    f"{name!r} unexpected_existing_split {plan.why} "
                    "— CHECK THE DRAFT IN ADP",
                    flush=True,
                )
                continue
            base_idx = plan.base_index
            base_reg = plan.base_hours
            keep, premium = rate2_split(
                adp_regular_hours=base_reg, solo_hours=solo
            )
            if premium <= 0:
                continue
            try:
                if plan.reuse_index is not None:
                    # An earlier failed run can leave empty rate lines behind.
                    # Reuse one instead of adding another: deleting rows is a
                    # destructive path we do not need, and adding more would let
                    # the debris grow on every attempt.
                    print(
                        f"[adp_payroll_draft] BREADCRUMB solo_rate2_reusing_empty_row "
                        f"{name!r} row={plan.reuse_index}"
                    )
                    base_now, new_now = base_idx, plan.reuse_index
                else:
                    rows_before = len(_ag_grid_rows(page))
                    if not _open_row_action_menu(page, row_index=base_idx):
                        raise RuntimeError("no_row_menu")
                    page.wait_for_timeout(600)
                    if not _click_menu_item(
                        page,
                        "Add row",
                        test_id="optionsAddRowButton",
                        row_index=base_idx,
                    ):
                        raise RuntimeError("no_add_row")
                    page.wait_for_timeout(1_500)
                    _dismiss_adp_error_dialog(page)
                    # Distinguish "no row appeared" from "a row appeared on
                    # someone else", which is what a document-wide menu lookup
                    # used to do and which otherwise reads identically from this
                    # employee's side.
                    grew = len(_ag_grid_rows(page)) - rows_before
                    mine = _employee_line_count(page, name)
                    if grew > 0 and mine < 2:
                        raise RuntimeError(
                            f"add_row_landed_elsewhere grew={grew} mine={mine}"
                        )

                    # Re-resolve BOTH indices from the post-insert grid. The
                    # original row can move too, so neither the remembered base
                    # index nor "whatever is not the base index" is trustworthy.
                    base_now, new_now = _rate2_row_indices(
                        page, employee=name, base_reg=base_reg
                    )
                    if new_now is None or base_now is None:
                        raise RuntimeError(
                            f"no_new_row lines={_employee_line_count(page, name)}"
                        )
                if not _select_available_rate(
                    page, row_index=new_now, rate_dollars=premium_rate
                ):
                    raise RuntimeError("no_rate_pick")
                page.wait_for_timeout(600)
                # Premium first, base reduced second: a crash between the two
                # leaves total hours too high, which the guardrail catches, rather
                # than too low, which looks like a self-consistent short paycheck.
                _fill_grid_amount(
                    page, employee=name, col_id="REGH",
                    amount=premium, row_index=new_now,
                )
                _fill_grid_amount(
                    page, employee=name, col_id="REGH",
                    amount=keep, row_index=base_now,
                )
                page.wait_for_timeout(600)
                # Count only rows that carry hours. Leftover empty rate lines are
                # debris, and counting them failed Perales at exactly the moment
                # her split had in fact landed correctly (live 2026-09-21).
                #
                # Read more than once: AG Grid repaints asynchronously after a
                # cell edit, and a continuation row mid-repaint has no body cells,
                # so it reads as absent. That is what failed Huynh with
                # "total_changed before=29.52 after=28.55" on a split that had
                # actually landed — the 0.97 row simply had not painted yet.
                after_rows: list[dict] = []
                after_total = 0.0
                for attempt in range(3):
                    after_rows = [
                        r for r in _employee_rows(page, name) if not _row_is_empty(r)
                    ]
                    after_total = round(
                        sum(float(r.get("reg") or 0) for r in after_rows), 2
                    )
                    if len(after_rows) == 2 and abs(after_total - base_reg) < 0.011:
                        break
                    if attempt < 2:
                        page.wait_for_timeout(900)
                if len(after_rows) != 2:
                    raise RuntimeError(
                        f"expected_2_funded_lines got={len(after_rows)} "
                        f"total={after_total}"
                    )
                if abs(after_total - base_reg) >= 0.011:
                    raise RuntimeError(
                        f"total_changed before={base_reg} after={after_total}"
                    )
                applied.append(name)
                print(
                    f"[adp_payroll_draft] BREADCRUMB solo_rate2_applied {name!r} "
                    f"rate1={keep} rate2={premium} total={base_reg}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}: {exc}")
                print(
                    f"[adp_payroll_draft] BREADCRUMB solo_rate2_failed {name!r} "
                    f"{type(exc).__name__}: {exc} — CHECK THE DRAFT IN ADP",
                    flush=True,
                )
        nxt = page.locator("[data-test-id='pagination-chevron-right']").first
        try:
            if nxt.is_visible() and nxt.is_enabled():
                nxt.click()
                page.wait_for_timeout(1_000)
                continue
        except Exception:  # noqa: BLE001
            break
        break

    return {
        "applied": applied,
        "failed": failed,
        "premium_hours": round(sum(wanted.values()), 2),
    }


def _employee_rows(page, employee: str) -> list[dict]:
    """Every line item currently on screen for one employee, from a fresh read."""
    want = name_key(employee)
    return [
        r for r in _ag_grid_rows(page)
        if name_key(r.get("employee") or "") == want
    ]


def _employee_line_count(page, employee: str) -> int:
    return len(_employee_rows(page, employee))


def _rate2_row_indices(
    page, *, employee: str, base_reg: float
) -> tuple[str | None, str | None]:
    """Locate (original, newly-added) row indices after an "Add row".

    Identified by content, not position: the original is the row still carrying
    the pre-split Regular hours, the new one is the empty row. Position is not
    usable because the insert renumbers rows, and "the index that is not the old
    base index" silently resolves to the original when the insert lands above it.
    """
    rows = _employee_rows(page, employee)
    if len(rows) != 2:
        return (None, None)
    base = next(
        (r for r in rows if abs(float(r.get("reg") or 0) - base_reg) < 0.011), None
    )
    if base is None:
        return (None, None)
    new = next((r for r in rows if r is not base), None)
    if new is None:
        return (None, None)
    return (str(base.get("row_index")), str(new.get("row_index")))


def _new_row_index_for(page, *, employee: str, exclude: str) -> str | None:
    """The row-index of an employee's newly added line item."""
    for row in _ag_grid_rows(page):
        if name_key(row.get("employee") or "") != name_key(employee):
            continue
        idx = str(row.get("row_index"))
        if idx != exclude:
            return idx
    return None


def _preview_pay_rows(page) -> dict[str, dict[str, float]]:
    raw = page.evaluate(
        """() => {
          const num = (t) => {
            const s = (t || '').replace(/[$,]/g, '').trim();
            const n = parseFloat(s);
            return Number.isFinite(n) ? n : 0;
          };
          const headers = [...document.querySelectorAll(
            '[role="columnheader"], th'
          )].map(h => (h.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase());
          const hi = (needles) => {
            for (let i = 0; i < headers.length; i++) {
              if (needles.some(n => headers[i].includes(n))) return i;
            }
            return -1;
          };
          const iHrs = hi(['total hours', 'hours']);
          const iGross = hi(['gross']);
          const out = {};
          const rows = document.querySelectorAll(
            '.ag-pinned-left-cols-container [role="row"], table tbody tr, [role="row"]'
          );
          /* Prefer preview grid: name pinned, hours/gross in center. */
          for (const row of document.querySelectorAll(
            '.ag-pinned-left-cols-container [role="row"]'
          )) {
            const idx = row.getAttribute('row-index');
            const name = (row.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!name.includes(',')) continue;
            const body = document.querySelector(
              '.ag-center-cols-container [role="row"][row-index="' + idx + '"]'
            );
            const cells = body
              ? [...body.querySelectorAll('[role="gridcell"]')].map(c => (c.innerText || '').trim())
              : [];
            out[name.split('\\n')[0].trim()] = {
              hours: iHrs >= 0 && cells[iHrs] != null ? num(cells[iHrs]) : num(cells[0] || ''),
              gross: iGross >= 0 && cells[iGross] != null ? num(cells[iGross]) : 0,
            };
          }
          if (Object.keys(out).length) return { headers, rows: out };
          for (const tr of document.querySelectorAll('table tr')) {
            const cells = [...tr.querySelectorAll('td, th, [role="gridcell"]')]
              .map(td => (td.innerText || '').trim());
            const blob = (tr.innerText || '').replace(/\\s+/g, ' ').trim();
            const nameCell = (cells[0] || blob).split('\\n')[0].trim();
            if (!nameCell.includes(',')) continue;
            const name = nameCell.replace(/\\s*regular\\b.*$/i, '').trim();
            out[name] = {
              hours: iHrs >= 0 && cells[iHrs] ? num(cells[iHrs]) : num(cells[2] || ''),
              gross: iGross >= 0 && cells[iGross] ? num(cells[iGross]) : num(cells[3] || ''),
            };
          }
          return { headers, rows: out };
        }"""
    )
    print(f"[adp_payroll_draft] preview_rows {raw}")
    if not isinstance(raw, dict):
        return {}
    return raw.get("rows") or {}


def combine_preview_totals(
    rows: dict[str, dict[str, float]],
    footer: dict[str, float | None] | None = None,
) -> tuple[float | None, float | None]:
    """Prefer Preview footer Total hours / Gross pay; else sum grid rows."""
    footer = footer or {}
    row_h = round(sum(float(r.get("hours") or 0) for r in rows.values()), 2)
    row_g = round(sum(float(r.get("gross") or 0) for r in rows.values()), 2)
    fh = footer.get("hours")
    fg = footer.get("gross")
    if fh not in (None,) and float(fh or 0) > 0:
        hours = round(float(fh), 2)
    elif row_h:
        hours = row_h
    else:
        hours = None
    if fg not in (None,) and float(fg or 0) > 0:
        gross = round(float(fg), 2)
    elif row_g:
        gross = row_g
    else:
        gross = None
    return hours, gross


def _preview_footer_totals(page) -> dict[str, float | None]:
    raw = page.evaluate(
        """() => {
          const num = (t) => {
            const s = (t || '').replace(/[$,]/g, '').trim();
            const n = parseFloat(s);
            return Number.isFinite(n) ? n : null;
          };
          const text = document.body.innerText || '';
          const hours = text.match(/Total hours\\s+([0-9,.]+)/i);
          const gross = text.match(/Gross pay\\s+\\$?([0-9,.]+)/i);
          return {
            hours: hours ? num(hours[1]) : null,
            gross: gross ? num(gross[1]) : null,
          };
        }"""
    )
    print(f"[adp_payroll_draft] preview_footer {raw}")
    if not isinstance(raw, dict):
        return {"hours": None, "gross": None}
    return {
        "hours": raw.get("hours"),
        "gross": raw.get("gross"),
    }


def _print_hours_wages_compare(
    packet: list[PayrollPacketRow],
    adp_hours: dict[str, float],
    preview: dict[str, dict[str, float]],
) -> None:
    print("[adp_payroll_draft] COMPARE hours (console vs ADP Enter) and wages vs Preview Gross")
    print(
        "[adp_payroll_draft] NOTE wages≠Gross: Gross = wages + tips + bonus + perks"
    )
    adp_h = {name_key(k): (k, v) for k, v in adp_hours.items()}
    prev_k = {name_key(k): (k, v) for k, v in preview.items()}
    for row in packet:
        key = name_key(row.employee)
        ours_h = round(row.regular_hours + row.ot_hours, 2)
        ah = adp_h.get(key, (None, None))[1]
        pr = prev_k.get(key, (None, {}))[1] or {}
        ph = pr.get("hours")
        pg = pr.get("gross")
        ours_total = round(
            (row.est_wages_dollars or 0)
            + row.tips_dollars
            + row.bonus_dollars
            + row.misc_reimbursement_dollars,
            2,
        )
        h_ok = ah is not None and abs(ours_h - ah) <= HOURS_TOLERANCE_HOURS
        w_ok = (
            pg is not None
            and abs(ours_total - pg) <= WAGE_TOLERANCE_DOLLARS
        )
        print(
            f"[adp_payroll_draft] COMPARE {row.employee}: "
            f"hours our={ours_h} adp={ah} {'OK' if h_ok else 'DIFF'} | "
            f"wages={row.est_wages_dollars} tips={row.tips_dollars} "
            f"bonus={row.bonus_dollars} perk={row.misc_reimbursement_dollars} | "
            f"total_pay our={ours_total} preview_gross={pg} {'OK' if w_ok else 'DIFF'}"
        )
    n_h = sum(
        1
        for row in packet
        if abs(
            (adp_h.get(name_key(row.employee), (None, None))[1] or -999)
            - round(row.regular_hours + row.ot_hours, 2)
        )
        <= HOURS_TOLERANCE_HOURS
    )
    n_g = sum(
        1
        for row in packet
        if (prev_k.get(name_key(row.employee), (None, {}))[1] or {}).get("gross")
        is not None
        and abs(
            round(
                (row.est_wages_dollars or 0)
                + row.tips_dollars
                + row.bonus_dollars
                + row.misc_reimbursement_dollars,
                2,
            )
            - float((prev_k.get(name_key(row.employee), (None, {}))[1] or {}).get("gross"))
        )
        <= WAGE_TOLERANCE_DOLLARS
    )
    print(
        f"[adp_payroll_draft] COMPARE_SUMMARY hours_ok={n_h}/{len(packet)} "
        f"gross_ok={n_g}/{len(packet)}"
    )


def _dismiss_early_run_modal(page) -> None:
    """Mid-period: ADP asks Off-cycle vs continue regular. Never Off-cycle."""
    no = page.get_by_role(
        "button", name="No, Continue with this Regular Payroll"
    ).first
    try:
        no.wait_for(state="visible", timeout=8_000)
    except Exception:  # noqa: BLE001
        return
    abort_if_forbidden_label(no.inner_text() or "No, Continue")
    print("[adp_payroll_draft] BREADCRUMB continue regular (not Off-cycle)")
    no.click()
    page.wait_for_timeout(2_000)


def _dismiss_adp_error_dialog(page) -> bool:
    """OK/Close on ADP 'Something isn't quite right'. Never Save."""
    try:
        if not page.get_by_text("Something isn't quite right").first.is_visible():
            return False
    except Exception:  # noqa: BLE001
        return False
    for name in (r"^OK$", r"^Close$", r"^Got it$"):
        btn = page.get_by_role("button", name=re.compile(name, re.I)).first
        try:
            if btn.is_visible():
                abort_if_forbidden_label(btn.inner_text() or "OK")
                print("[adp_payroll_draft] BREADCRUMB dismiss ADP error dialog")
                btn.click()
                page.wait_for_timeout(1_500)
                return True
        except Exception:  # noqa: BLE001
            continue
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    return True


def _import_with_cancel_retry(page, *, allow_start: bool) -> None:
    """If Import is blocked (error modal / stuck wizard), Cancel and Start again."""
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            _dismiss_adp_error_dialog(page)
            _click_import_not_finish(page)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(
                f"[adp_payroll_draft] BREADCRUMB import_fail attempt={attempt}/3 "
                f"({exc}); cancel and retry"
            )
            _click_delete_in_progress(page)
            _open_payroll_home(page)
            if allow_start:
                _click_start_if_present(page)
                try:
                    _wait_wizard_ready(page)
                except Exception as wait_exc:  # noqa: BLE001
                    print(f"[adp_payroll_draft] wizard wait after retry ({wait_exc})")
            page.wait_for_timeout(1_000)
    raise RuntimeError(f"import_retry_exhausted {last}") from last


def _zero_hours_not_on_console(page, ours: dict[str, float]) -> int:
    """Clear REGH/PERSH/HOLH/OT for ADP names with hours we do not have (1:1)."""
    ours_k = {name_key(k): v for k, v in ours.items()}
    zeroed = 0
    for _ in range(6):
        page_map = _ag_enter_page_hours(page)
        for name, rec in page_map.items():
            ah = float(rec.get("hours") or 0)
            oh = float(ours_k.get(name_key(name), 0.0) or 0.0)
            if ah <= 0 or oh > 0:
                continue
            print(
                f"[adp_payroll_draft] BREADCRUMB zero_stale_hours {name!r} "
                f"adp={ah} our={oh} line_items={len(rec.get('rows') or [])}"
            )
            # Every line item, not just the first: a two-rate employee's premium
            # row would otherwise survive the clear and still get paid.
            for line in rec.get("rows") or []:
                idx = line.get("row_index")
                for col_id, key in (
                    ("REGH", "reg"),
                    ("PERSH", "pers"),
                    ("HOLH", "hol"),
                ):
                    if float(line.get(key) or 0) > 0:
                        _fill_grid_amount(
                            page, employee=name, col_id=col_id,
                            amount=0.0, row_index=idx,
                        )
                if float(line.get("ot") or 0) > 0:
                    for col_id in ("OVTH", "NQOVTH"):
                        _fill_grid_amount(
                            page, employee=name, col_id=col_id,
                            amount=0.0, row_index=idx,
                        )
            zeroed += 1
        nxt = page.locator("[data-test-id='pagination-chevron-right']").first
        try:
            if nxt.is_visible() and nxt.is_enabled():
                nxt.click()
                page.wait_for_timeout(800)
                continue
        except Exception:  # noqa: BLE001
            break
        break
    prev = page.locator("[data-test-id='pagination-chevron-left']").first
    for _ in range(6):
        try:
            if prev.is_visible() and prev.is_enabled():
                prev.click()
                page.wait_for_timeout(400)
            else:
                break
        except Exception:  # noqa: BLE001
            break
    return zeroed


def _click_import_not_finish(page) -> None:
    import re

    latest = page.get_by_role(
        "button", name=re.compile(r"Import latest timecards", re.I)
    ).first
    try:
        if latest.is_visible():
            abort_if_forbidden_label(latest.inner_text() or "Import latest timecards")
            print("[adp_payroll_draft] BREADCRUMB Import latest timecards (not Skip)")
            latest.click()
            page.wait_for_timeout(3_000)
            _dismiss_early_run_modal(page)
            page.get_by_role("button", name="Preview payroll").wait_for(
                state="visible", timeout=60_000
            )
            return
    except Exception:  # noqa: BLE001
        pass
    imp = page.locator("[data-test-id='timeImportButton']").first
    imp.wait_for(state="visible", timeout=15_000)
    abort_if_forbidden_label(imp.inner_text() or "Import")
    print("[adp_payroll_draft] BREADCRUMB clicking Import (not Finish later)")
    imp.click()
    page.wait_for_timeout(3_000)
    _dismiss_early_run_modal(page)
    page.get_by_role("button", name="Preview payroll").wait_for(
        state="visible", timeout=60_000
    )


def _click_preview_only(page) -> None:
    """Preview payroll button on Enter payroll. Never Save / Finish Later / Approve."""
    _dismiss_early_run_modal(page)
    btn = page.locator("[data-test-id='pdeNextButton']").first
    try:
        btn.wait_for(state="visible", timeout=15_000)
    except Exception:
        btn = page.get_by_role("button", name="Preview payroll").first
        btn.wait_for(state="visible", timeout=15_000)
    abort_if_forbidden_label(btn.inner_text() or "Preview payroll")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    print("[adp_payroll_draft] BREADCRUMB clicking Preview payroll")
    btn.click()
    _wait_preview_ready(page)


def _wait_preview_ready(page) -> None:
    for _ in range(45):
        try:
            if page.get_by_text("Processing", exact=True).first.is_visible():
                page.wait_for_timeout(1_000)
                continue
        except Exception:  # noqa: BLE001
            pass
        try:
            if (
                page.get_by_text("Does this look right?").first.is_visible()
                or page.get_by_text("Payrun Total").first.is_visible()
                or page.get_by_text("Gross pay").first.is_visible()
            ):
                print(f"[adp_payroll_draft] preview_ready url={page.url}")
                return
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1_000)
    print("[adp_payroll_draft] BREADCRUMB preview_wait_timeout")


def _dismiss_looks_right_for_screenshot(page) -> None:
    """Close ADP's issues modal so the Preview table is visible. Never Approve."""
    modal = page.get_by_text("Does this look right?").first
    try:
        if not modal.is_visible():
            return
    except Exception:  # noqa: BLE001
        return
    ignore = page.get_by_role("button", name="Ignore issues").first
    try:
        ignore.wait_for(state="visible", timeout=5_000)
        abort_if_forbidden_label(ignore.inner_text() or "Ignore issues")
        print("[adp_payroll_draft] BREADCRUMB Ignore issues (not Approve)")
        ignore.click()
        page.wait_for_timeout(2_000)
    except Exception as exc:  # noqa: BLE001
        print(f"[adp_payroll_draft] ignore_issues skip ({exc})")


def _grid_headers(page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('[role="columnheader"], th')]
          .map(h => (h.innerText || '').trim())
          .filter(Boolean)"""
    ) or []


def _row_index_for_employee(page, employee: str) -> str | None:
    last = employee.split(",")[0].strip().lower()
    return page.evaluate(
        """(last) => {
          for (const row of document.querySelectorAll(
            '.ag-pinned-left-cols-container [role="row"]'
          )) {
            const txt = (row.innerText || '').replace(/\\s+/g, ' ').toLowerCase();
            if (txt.includes(last)) return row.getAttribute('row-index');
          }
          return null;
        }""",
        last,
    )


def _read_grid_cell(page, *, row_index: str, col_id: str) -> str:
    return (
        page.evaluate(
            """({ idx, colId }) => {
              const body = document.querySelector(
                '.ag-center-cols-container [role="row"][row-index="' + idx + '"]'
              );
              if (!body) return '';
              const cell = body.querySelector('[col-id="' + colId + '"]');
              return cell ? (cell.innerText || '').trim() : '';
            }""",
            {"idx": row_index, "colId": col_id},
        )
        or ""
    )


def _ensure_ag_col_visible(page, col_id: str) -> bool:
    """Scroll the Enter-payroll AG Grid until col_id is in the DOM. Never Customize/Save."""
    info = page.evaluate(
        """(colId) => {
          const headerIds = () => [...document.querySelectorAll('.ag-header-cell')]
            .map(h => h.getAttribute('col-id') || '');
          const hit = () => document.querySelector('[col-id="' + colId + '"]');
          if (hit()) {
            hit().scrollIntoView({ inline: 'center', block: 'nearest' });
            return { ok: true, how: 'already', ids: headerIds() };
          }
          const root = document.querySelector('.ag-root');
          const api = root && (root.__agComponent && root.__agComponent.api
            || root.__agGridInstance && root.__agGridInstance.api);
          if (api && typeof api.ensureColumnVisible === 'function') {
            try { api.ensureColumnVisible(colId); } catch (e) {}
            if (hit()) {
              hit().scrollIntoView({ inline: 'center', block: 'nearest' });
              return { ok: true, how: 'ensureColumnVisible', ids: headerIds() };
            }
          }
          const vp = document.querySelector('.ag-center-cols-viewport')
            || document.querySelector('.ag-body-horizontal-scroll-viewport');
          if (!vp) return { ok: false, how: 'no-viewport', ids: headerIds() };
          vp.scrollLeft = 0;
          for (let i = 0; i < 80; i++) {
            vp.scrollLeft += 140;
            if (hit()) {
              hit().scrollIntoView({ inline: 'center', block: 'nearest' });
              return { ok: true, how: 'scroll-' + i, ids: headerIds() };
            }
          }
          return {
            ok: false, how: 'not-in-grid', ids: headerIds(),
            scrollLeft: vp.scrollLeft, scrollWidth: vp.scrollWidth,
          };
        }""",
        col_id,
    )
    print(f"[adp_payroll_draft] ensure_col {col_id} {info}", flush=True)
    return bool(isinstance(info, dict) and info.get("ok"))


def _fill_grid_amount(
    page,
    *,
    employee: str,
    col_id: str,
    amount: float,
    row_index: str | None = None,
) -> bool:
    """Playwright-dblclick the AG Grid body cell (pinned names, center money).

    ``row_index`` addresses one specific line item. Without it the name lookup
    returns the employee's first row, which is wrong for anyone paid at two
    rates.
    """
    idx = row_index if row_index is not None else _row_index_for_employee(page, employee)
    if idx is None:
        print(f"[adp_payroll_draft] no_cell {employee} {col_id} {{'ok': False, 'why': 'no_name'}}")
        return False
    cell = page.locator(
        f'.ag-center-cols-container [role="row"][row-index="{idx}"] [col-id="{col_id}"]'
    ).first
    try:
        _ensure_ag_col_visible(page, col_id)
        cell.scroll_into_view_if_needed(timeout=8_000)
        cell.dblclick(timeout=5_000)
    except Exception as exc:  # noqa: BLE001
        print(f"[adp_payroll_draft] no_cell {employee} {col_id} dblclick {exc}")
        return False
    page.wait_for_timeout(250)
    typed = f"{amount:.2f}"
    page.keyboard.press("Meta+A")
    page.keyboard.type(typed, delay=20)
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)
    shown = _read_grid_cell(page, row_index=idx, col_id=col_id)
    print(
        f"[adp_payroll_draft] cell_after {employee} {col_id} typed={typed} shown={shown!r}",
        flush=True,
    )
    digits = "".join(ch for ch in shown if ch.isdigit() or ch in ".-")
    try:
        return abs(float(digits or "0") - amount) < 0.02
    except ValueError:
        return False


def _fill_col_ids(page) -> dict[str, str]:
    headers = page.evaluate(
        """() => [...document.querySelectorAll('.ag-header-cell')].map(h => ({
          id: h.getAttribute('col-id') || '',
          text: (h.innerText || '').trim(),
        }))"""
    ) or []
    print(f"[adp_payroll_draft] ag_headers {headers}", flush=True)
    out: dict[str, str] = {}
    for h in headers:
        hid, text = (h.get("id") or "", h.get("text") or "")
        blob = f"{hid} {text}"
        if header_index([blob], ("nqcc", "tips owed", "nqcredtp")) is not None:
            out["tip"] = hid
        if header_index([blob], ("misc reimb", "advnta")) is not None:
            out["misc"] = hid
        if (
            header_index([blob], ("bonus",)) is not None
            and "ovrtm" not in blob.lower()
        ):
            out["bonus"] = hid
    out.setdefault("tip", "NQCREDTPPA")
    out.setdefault("misc", "ADVNTA")
    out.setdefault("bonus", "BONA")
    return out


def _fill_money_lines(page, packet: list[PayrollPacketRow]) -> int:
    """Type tips / Bonus / Misc Reimb on the Enter payroll grid. Never hours."""
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    filled = 0
    for page_i in range(2):
        ids = _fill_col_ids(page)
        print(f"[adp_payroll_draft] fill_col_ids {ids}", flush=True)
        bonus_id = ids.get("bonus") or "BONA"
        _ensure_ag_col_visible(page, bonus_id)
        ids = _fill_col_ids(page)
        print(f"[adp_payroll_draft] fill_col_ids_after_bonus_scroll {ids}", flush=True)
        for row in packet:
            amounts = {
                "tip": row.tips_dollars,
                "bonus": row.bonus_dollars,
                "misc": row.misc_reimbursement_dollars,
            }
            for kind, amt in amounts.items():
                if amt <= 0:
                    continue
                col_id = ids.get(kind)
                if not col_id:
                    continue
                try:
                    abort_if_forbidden_label(kind)
                    if _fill_grid_amount(
                        page, employee=row.employee, col_id=col_id, amount=amt
                    ):
                        filled += 1
                        print(
                            f"[adp_payroll_draft] filled {row.employee!r} {kind}={amt:.2f}",
                            flush=True,
                        )
                except Exception as exc:  # noqa: BLE001
                    print(f"[adp_payroll_draft] fill_skip {row.employee} {kind}: {exc}")
        nxt = page.locator("[data-test-id='pagination-chevron-right']").first
        try:
            if nxt.is_visible() and nxt.is_enabled():
                nxt.click()
                page.wait_for_timeout(1_000)
                continue
        except Exception:  # noqa: BLE001
            break
        break
    prev = page.locator("[data-test-id='pagination-chevron-left']").first
    for _ in range(4):
        try:
            if prev.is_visible() and prev.is_enabled():
                prev.click()
                page.wait_for_timeout(400)
            else:
                break
        except Exception:  # noqa: BLE001
            break
    return filled


def _click_delete_in_progress(page) -> bool:
    import re

    _dismiss_early_run_modal(page)
    prev = page.locator("[data-test-id='previousPDEButton']").first
    try:
        if prev.is_visible():
            abort_if_forbidden_label(prev.inner_text() or "Previous")
            print("[adp_payroll_draft] BREADCRUMB Previous from Preview (not Approve)")
            prev.click()
            page.wait_for_timeout(2_000)
    except Exception:  # noqa: BLE001
        pass
    cancel = page.locator("[data-test-id='cancelPDEButton']").first
    try:
        if cancel.is_visible():
            abort_if_forbidden_label(cancel.inner_text() or "Cancel")
            print("[adp_payroll_draft] BREADCRUMB clicking Cancel (discard; not Save)")
            cancel.click()
            page.wait_for_timeout(1_000)
            for conf in (
                r"^(Yes|Discard|Don't save|Delete)$",
                r"discard",
            ):
                yes = page.get_by_role("button", name=re.compile(conf, re.I)).first
                try:
                    if yes.is_visible():
                        abort_if_forbidden_label(yes.inner_text() or "Yes")
                        yes.click()
                        page.wait_for_timeout(2_000)
                        return True
                except Exception:  # noqa: BLE001
                    continue
            return True
    except Exception:  # noqa: BLE001
        pass
    for name in (r"^Delete$", r"Delete payroll", r"Cancel payroll"):
        btn = page.get_by_role("button", name=re.compile(name, re.I)).first
        try:
            if not btn.is_visible():
                continue
            label = btn.inner_text() or "Delete"
            abort_if_forbidden_label(label)
            print(f"[adp_payroll_draft] BREADCRUMB clicking Delete {label!r}")
            btn.click()
            page.wait_for_timeout(1_000)
            yes = page.get_by_role("button", name=re.compile(r"^(Yes|Delete|Confirm)$", re.I)).first
            try:
                if yes.is_visible():
                    abort_if_forbidden_label(yes.inner_text() or "Yes")
                    yes.click()
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(2_000)
            return True
        except Exception:  # noqa: BLE001
            continue
    print("[adp_payroll_draft] BREADCRUMB delete_not_found — closing without Finish later")
    return False


def run_live_preview(
    *,
    store: str,
    hold_seconds: int = 180,
    allow_start: bool = False,
    packet: list[PayrollPacketRow] | None = None,
    period_start: str = "",
    period_end: str = "",
    delete_after: bool = False,
    solo_gap: list[str] | None = None,
) -> dict[str, Any]:
    """Login → Run payroll → hours guardrail → Import/fill → Preview → leave draft.

    Mid-period (as-of before period_end): Slack mismatches as WARN and still Preview.
    Period-end: Slack and skip fill. Never Finish Later / Approve / Submit / Save.
    ``delete_after`` is optional cleanup; default is leave In Progress for the operator.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from skills.adp_run_automation.runner import adp_session  # noqa: PLC0415

    packet = packet or []
    today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    strict = bool(period_end) and today >= period_end
    shots: list[str] = []
    guardrail_fails: list[str] = []
    deleted = False
    headed = _adp_headed()
    preview_url = ""
    payroll_home_url = ""
    preview_hours: float | None = None
    preview_gross: float | None = None
    started = False
    print(
        f"[adp_payroll_draft] headed={headed} "
        "(set BHAGA_ADP_HEADED=1 for a visible browser)"
    )

    with adp_session(
        store=store,
        headed=headed,
        slow_mo_ms=50 if headed else 0,
    ) as (_ctx, page):
        _open_payroll_home(page)
        payroll_home_url = page.url
        shots.append(screenshot_preview(page, "home"))
        _dismiss_adp_error_dialog(page)
        if allow_start:
            started = _click_start_if_present(page)
            if started:
                try:
                    _wait_wizard_ready(page)
                except Exception as exc:  # noqa: BLE001
                    print(f"[adp_payroll_draft] wizard wait ({exc}); continuing")
                shots.append(screenshot_preview(page, "after-start"))
                for lab in _visible_action_labels(page):
                    print(f"[adp_payroll_draft] after_start control {lab!r}")
        fill_ok = True
        try:
            _dismiss_adp_error_dialog(page)
            _import_with_cancel_retry(page, allow_start=allow_start)
            shots.append(screenshot_preview(page, "after-import"))
            ours = {
                r.employee: round(r.regular_hours + r.ot_hours, 2) for r in packet
            }
            nzero = _zero_hours_not_on_console(page, ours)
            print(f"[adp_payroll_draft] zeroed_stale_hour_rows={nzero}")
            adp_hours = _paginate_timecard_hours(page)
            print(f"[adp_payroll_draft] adp_enter_hours {adp_hours}")
            if not adp_hours:
                guardrail_fails = ["timecard_parse_empty"]
            else:
                guardrail_fails = hours_guardrail_failures(ours, adp_hours)
            if guardrail_fails:
                _slack_guardrail(
                    f"{period_start}..{period_end}",
                    guardrail_fails,
                    strict=strict,
                )
            fill_ok = True
            if guardrail_fails:
                print(
                    "[adp_payroll_draft] BREADCRUMB hours_guardrail still filling "
                    "tips/bonus/perks (hours not overwritten)"
                )
            for lab in _visible_action_labels(page):
                print(f"[adp_payroll_draft] after_import control {lab!r}")
            solo_by_name = {
                r.employee: float(r.solo_premium_hours or 0) for r in packet
            }
            blocking = (
                unrepairable_hours_fails(ours, adp_hours, solo_by_name)
                if guardrail_fails and adp_hours
                else guardrail_fails
            )
            if guardrail_fails and not blocking:
                print(
                    "[adp_payroll_draft] BREADCRUMB hours_guardrail_repairable "
                    f"n={len(guardrail_fails)} every mismatch is a half-applied "
                    "rate-2 split; running the repair and re-checking hours"
                )
            if fill_ok and not blocking and not solo_gap and solo_rate2_enabled():
                # `fill_ok` stays True through a guardrail failure on purpose —
                # money columns are still safe to fill. The hours split is not, so
                # it needs its own gate: rewriting the Regular cell on a grid we
                # already know disagrees with the console compounds one wrong
                # number with another. The first live proof ran with 7 guardrail
                # failures outstanding because this read `fill_ok` alone.
                #
                # The one exception is a mismatch that *is* a half-applied split:
                # blocking on it deadlocks, since the broken state can then only
                # ever be fixed by hand. Hours are re-read below either way.
                rate2 = _apply_solo_rate2(
                    page, packet, premium_rate=_solo_premium_rate(store)
                )
                print(
                    f"[adp_payroll_draft] BREADCRUMB solo_rate2 "
                    f"applied={len(rate2['applied'])} failed={len(rate2['failed'])} "
                    f"hours={rate2['premium_hours']}"
                )
                # Re-read the hours: a correct split never changes an employee's
                # total, so the guardrail is the check that the split landed
                # right — and the one that clears a repaired mismatch.
                adp_hours = _paginate_timecard_hours(page) or adp_hours
                guardrail_fails = hours_guardrail_failures(ours, adp_hours)
                print(
                    "[adp_payroll_draft] BREADCRUMB post_rate2_guardrail "
                    f"n={len(guardrail_fails)} hours={adp_hours}"
                )
                if rate2["failed"]:
                    guardrail_fails = guardrail_fails + [
                        f"solo_rate2:{f}" for f in rate2["failed"]
                    ]
                shots.append(screenshot_preview(page, "after-solo-rate2"))
            elif any(float(r.solo_premium_hours or 0) > 0 for r in packet):
                why = (
                    "hours_guardrail" if guardrail_fails
                    else "solo_hours_stale" if solo_gap
                    else "flag_off" if not solo_rate2_enabled()
                    else "fill_not_ok"
                )
                print(
                    f"[adp_payroll_draft] BREADCRUMB solo_rate2_skipped why={why} "
                    "the rate-1/rate-2 lines are printed above for manual keying"
                )
            if fill_ok:
                nfill = _fill_money_lines(page, packet)
                print(f"[adp_payroll_draft] filled_fields={nfill}")
                shots.append(screenshot_preview(page, "filled-enter"))
            else:
                print("[adp_payroll_draft] BREADCRUMB skip_fill hours_guardrail strict")
            _click_preview_only(page)
            shots.append(screenshot_preview(page, "preview-modal"))
            _dismiss_looks_right_for_screenshot(page)
            shots.append(screenshot_preview(page, "preview"))
            preview_url = operator_adp_preview_url(page.url, payroll_home_url)
            print(f"[adp_payroll_draft] preview_url {preview_url}")
            preview_rows: dict[str, dict[str, float]] = {}
            for _ in range(3):
                preview_rows.update(_preview_pay_rows(page))
                nxt = page.locator("[data-test-id='pagination-chevron-right']").first
                try:
                    if nxt.is_visible() and nxt.is_enabled():
                        nxt.click()
                        page.wait_for_timeout(800)
                        continue
                except Exception:  # noqa: BLE001
                    break
                break
            _print_hours_wages_compare(packet, adp_hours, preview_rows)
            footer = _preview_footer_totals(page)
            ph, pg = combine_preview_totals(preview_rows, footer)
            preview_hours, preview_gross = ph, pg
            print(
                f"[adp_payroll_draft] BREADCRUMB preview_totals "
                f"hours={ph} gross={pg}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[adp_payroll_draft] Preview path ({exc})")
            shots.append(screenshot_preview(page, "no-preview"))
        print(
            f"[adp_payroll_draft] BREADCRUMB preview_hold store={store} "
            f"secs={hold_seconds} headed={headed} no_save no_approve strict={strict}"
        )
        if headed and hold_seconds > 0:
            page.wait_for_timeout(hold_seconds * 1000)
        if delete_after:
            deleted = _click_delete_in_progress(page)
            shots.append(screenshot_preview(page, "after-delete"))
        else:
            print(
                "[adp_payroll_draft] BREADCRUMB leave_draft "
                "no Delete/Cancel/Save/Approve — operator reviews in ADP"
            )
    if not preview_url:
        preview_url = operator_adp_preview_url("", payroll_home_url)
    return {
        "started": started,
        "screenshots": shots,
        "saved": False,
        "approved": False,
        "deleted": deleted,
        "guardrail_fails": guardrail_fails,
        "preview_url": preview_url,
        "preview_hours": preview_hours,
        "preview_gross": preview_gross,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store", default="palmetto")
    p.add_argument("--period-start", default="")
    p.add_argument("--period-end", default="")
    p.add_argument(
        "--list-history",
        action="store_true",
        help="Read-only: dump Payroll Home completed-payrun tiles/links.",
    )
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--no-dry-run", action="store_true")
    p.add_argument("--allow-prod-draft", action="store_true")
    p.add_argument(
        "--hold-seconds",
        type=int,
        default=180,
        help="Pause after Preview only when BHAGA_ADP_HEADED=1 (debug).",
    )
    p.add_argument(
        "--allow-start",
        action="store_true",
        help="Click Start after Payroll Home (still never Save/Approve).",
    )
    p.add_argument(
        "--delete-after",
        action="store_true",
        help="Delete the In Progress worksheet after Preview (cleanup only).",
    )
    args = p.parse_args(argv)
    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")
    if args.list_history:
        dump_payroll_home_history(store=args.store)
        return 0
    if not args.period_start or not args.period_end:
        p.error("--period-start and --period-end are required unless --list-history")
    dry = not args.no_dry_run
    out = run_draft(
        store=args.store,
        period_start=args.period_start,
        period_end=args.period_end,
        dry_run=dry,
        allow_prod_draft=args.allow_prod_draft,
        hold_seconds=args.hold_seconds,
        allow_start=args.allow_start,
        keep_draft=not args.delete_after,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
