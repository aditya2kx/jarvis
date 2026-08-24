"""ADP RUN payroll draft: Start → fill Preview → Delete. Never Approve.

Issue #251. Default is dry-run (print packet, no ADP writes). Live Start requires
``--allow-prod-draft`` and will refuse Approve/Submit locators.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

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
    verb = "STOPPED (period-end)" if strict else "WARN (mid-period; still Preview+Delete)"
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
    keep_draft: bool = False,
    view_rows: list[dict[str, Any]] | None = None,
    hold_seconds: int = 180,
    allow_start: bool = False,
) -> dict[str, Any]:
    """Build the payroll packet. Live ADP Start is gated; dry-run never Starts."""
    if keep_draft:
        raise RuntimeError(
            "[adp_payroll_draft] BREADCRUMB keep_draft_forbidden "
            "evidence ladder must Delete"
        )
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
        print(
            f"  {row['employee']}: reg={row['regular_hours']} ot={row['ot_hours']} "
            f"rate={row['wage_rate']} wages={row.get('est_wages_dollars')} "
            f"tips={row.get('tips_dollars')} bonus={row['bonus_dollars']} "
            f"perk={row['misc_reimbursement_dollars']}"
        )
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
    live = run_live_preview(
        store=store,
        hold_seconds=hold_seconds,
        allow_start=allow_start,
        packet=packet,
        period_start=period_start,
        period_end=period_end,
        delete_after=True,
    )
    result["started"] = live.get("started", False)
    result["deleted"] = live.get("deleted", False)
    result["guardrail_fails"] = live.get("guardrail_fails", [])
    result["screenshots"] = live.get("screenshots", [])
    result["saved"] = False
    result["approved"] = False
    return result


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
    if rows:
        return rows
    # Open-period view often ends before the ADP biweek Sunday.
    return read_query(
        f"SELECT * FROM {fq('vw_model_payroll_period')} "
        f"WHERE period_start = DATE '{period_start}' "
        "ORDER BY employee"
    )


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


def _ag_enter_page_hours(page) -> dict[str, dict[str, float]]:
    """Paid hours on Enter payroll AG Grid: Regular + Personal + Holiday + OT."""
    raw = page.evaluate(
        """() => {
          const num = (t) => {
            const s = (t || '').replace(/[$,]/g, '').trim();
            const n = parseFloat(s);
            return Number.isFinite(n) ? n : 0;
          };
          const out = {};
          for (const row of document.querySelectorAll(
            '.ag-pinned-left-cols-container [role="row"]'
          )) {
            const idx = row.getAttribute('row-index');
            const name = (row.innerText || '').replace(/\\s+/g, ' ').trim()
              .replace(/\\s*\\$[\\d.]+\\s*\\/\\s*hr.*$/i, '')
              .trim();
            if (!name || !name.includes(',')) continue;
            const body = document.querySelector(
              '.ag-center-cols-container [role="row"][row-index="' + idx + '"]'
            );
            const cell = (id) => {
              const el = body && body.querySelector('[col-id="' + id + '"]');
              return el ? num(el.innerText) : 0;
            };
            const rateTxt = (row.innerText || '');
            const rateM = rateTxt.match(/\\$?([0-9]+\\.[0-9]+)/);
            const reg = cell('REGH');
            const pers = cell('PERSH');
            const hol = cell('HOLH');
            const ot = cell('OVTH') + cell('NQOVTH');
            out[name.split('\\n')[0].trim()] = {
              hours: Math.round((reg + pers + hol + ot) * 100) / 100,
              reg, pers, hol, ot,
              rate: rateM ? parseFloat(rateM[1]) : 0,
            };
          }
          return out;
        }"""
    )
    return raw or {}


def _paginate_timecard_hours(page) -> dict[str, float]:
    merged: dict[str, float] = {}
    detail: dict[str, dict[str, float]] = {}
    for _ in range(6):
        page_map = _ag_enter_page_hours(page)
        for name, rec in page_map.items():
            detail[name] = rec
            merged[name] = float(rec.get("hours") or 0)
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
    print(f"[adp_payroll_draft] ag_enter_hours {detail}")
    return merged


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
                f"adp={ah} our={oh}"
            )
            for col_id, key in (
                ("REGH", "reg"),
                ("PERSH", "pers"),
                ("HOLH", "hol"),
            ):
                if float(rec.get(key) or 0) > 0:
                    _fill_grid_amount(page, employee=name, col_id=col_id, amount=0.0)
            if float(rec.get("ot") or 0) > 0:
                _fill_grid_amount(page, employee=name, col_id="OVTH", amount=0.0)
                _fill_grid_amount(page, employee=name, col_id="NQOVTH", amount=0.0)
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


def _fill_grid_amount(page, *, employee: str, col_id: str, amount: float) -> bool:
    """Playwright-dblclick the AG Grid body cell (pinned names, center money)."""
    idx = _row_index_for_employee(page, employee)
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
    delete_after: bool = True,
) -> dict[str, Any]:
    """Login → Run payroll → hours guardrail → Import/fill → Preview → hold → Delete.

    Mid-period (as-of before period_end): Slack mismatches as WARN and still Preview.
    Period-end: Slack and skip fill. Never Finish Later / Approve / Submit.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from skills.adp_run_automation.runner import (  # noqa: PLC0415
        _ensure_logged_in,
        launch_persistent,
    )

    packet = packet or []
    today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    strict = bool(period_end) and today >= period_end
    shots: list[str] = []
    guardrail_fails: list[str] = []
    deleted = False
    import re

    with launch_persistent(portal="adp", headed=True, slow_mo_ms=50) as (_ctx, page):
        _ensure_logged_in(page, store=store)
        _open_payroll_home(page)
        shots.append(screenshot_preview(page, "home"))
        started = False
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
            print(f"[adp_payroll_draft] preview_url {page.url}")
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
        except Exception as exc:  # noqa: BLE001
            print(f"[adp_payroll_draft] Preview path ({exc})")
            shots.append(screenshot_preview(page, "no-preview"))
        print(
            f"[adp_payroll_draft] BREADCRUMB preview_hold store={store} "
            f"secs={hold_seconds} no_save no_approve strict={strict}"
        )
        page.wait_for_timeout(max(hold_seconds, 1) * 1000)
        if delete_after:
            deleted = _click_delete_in_progress(page)
            shots.append(screenshot_preview(page, "after-delete"))
    return {
        "started": started,
        "screenshots": shots,
        "saved": False,
        "approved": False,
        "deleted": deleted,
        "guardrail_fails": guardrail_fails,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store", default="palmetto")
    p.add_argument("--period-start", required=True)
    p.add_argument("--period-end", required=True)
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--no-dry-run", action="store_true")
    p.add_argument("--allow-prod-draft", action="store_true")
    p.add_argument(
        "--hold-seconds",
        type=int,
        default=180,
        help="Keep headed ADP Preview open so the operator can compare.",
    )
    p.add_argument(
        "--allow-start",
        action="store_true",
        help="Click Start after Payroll Home (still never Save/Approve).",
    )
    args = p.parse_args(argv)
    dry = not args.no_dry_run
    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")
    out = run_draft(
        store=args.store,
        period_start=args.period_start,
        period_end=args.period_end,
        dry_run=dry,
        allow_prod_draft=args.allow_prod_draft,
        hold_seconds=args.hold_seconds,
        allow_start=args.allow_start,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
