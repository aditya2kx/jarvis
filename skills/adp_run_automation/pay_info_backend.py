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
from typing import Optional

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
        m2 = re.search(r"\$?\s*([\d,]+\.\d{2,4})", blob)
        if m2 and ("hour" in blob.lower() or "pay" in blob.lower() or blob.strip().startswith("$")):
            rate = float(m2.group(1).replace(",", ""))
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
        "scraped_at_utc": now,
    }


def _close_overlays(page) -> None:
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    except Exception:  # noqa: BLE001
        pass


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
            directory.first.click(force=True, timeout=8_000)
            page.wait_for_timeout(3000)
        except Exception:  # noqa: BLE001
            pass


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


def scrape_one_pay_info(page, canonical_name: str, *, dashboard_url: str) -> dict:
    """People → directory search → Manage pay info / Payroll info; return rate fields.

    Calibrated against 2026-08-01 spike (Brooke $15.2500 on Payroll info input).
    """
    search_name = directory_search_name(canonical_name)
    last_name = search_name.split(",")[0].strip()

    page.goto(dashboard_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1500)
    _close_overlays(page)

    _open_people_home(page)

    search = _people_search_box(page)
    search.click(force=True)
    search.fill(last_name)
    page.wait_for_timeout(2500)

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
        link = page.get_by_role("link", name=re.compile(re.escape(search_name), re.I))
        if link.count() == 0:
            link = page.get_by_text(re.compile(re.escape(search_name), re.I))
        link.first.click(force=True, timeout=10_000)
        page.wait_for_timeout(4000)

    page.evaluate(
        """() => {
          const a = document.getElementById('EMPLOYEE_PAYROLL');
          if (a) { a.click(); return; }
          const hit = [...document.querySelectorAll('a,button,div,span,li')]
            .find(e => (e.innerText || '').trim() === 'Payroll info');
          if (hit) hit.click();
        }"""
    )
    page.wait_for_timeout(4000)
    try:
        page.mouse.wheel(0, 900)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(800)

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
    )
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
    )
    parsed = parse_hourly_pay_rate(text, input_values=inputs)
    return {
        "employee_name": canonical_name,
        "search_name": search_name,
        **parsed,
        "body_excerpt": text[:500],
        "inputs": inputs[:20],
    }


def scrape_pay_info_rates(
    page,
    names: list[str],
    *,
    dashboard_url: str,
    excluded: Optional[set[str]] = None,
) -> tuple[list[dict], dict[str, str]]:
    """Scrape Payroll info rates for names. Per-employee failures are soft."""
    excluded = excluded or set()
    out: list[dict] = []
    errors: dict[str, str] = {}
    for name in names:
        try:
            raw = scrape_one_pay_info(page, name, dashboard_url=dashboard_url)
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
                f" (added_on={raw.get('added_on')})"
            )
        except Exception as exc:  # noqa: BLE001
            errors[name] = f"{type(exc).__name__}: {exc}"
            print(f"[pay_info] FAIL {name}: {errors[name]}")
            try:
                ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                shot = (
                    pathlib.Path.home()
                    / ".bhaga"
                    / "state"
                    / "screenshots"
                    / f"adp-pay-info-fail-{name.replace(',', '').replace(' ', '_')}-{ts}.png"
                )
                shot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(shot), full_page=True)
                print(f"[pay_info] breadcrumb screenshot → {shot}")
            except Exception:  # noqa: BLE001
                pass
    if errors:
        print(f"[pay_info] {len(errors)} failure(s): {errors}")
    return out, errors


def write_pay_info_json(
    rates: list[dict],
    *,
    store: str = "palmetto",
    errors: Optional[dict[str, str]] = None,
    attempted: Optional[list[str]] = None,
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
            # Token hourlies on salaried Payroll-info pages (Lindsay $25 → $1.25).
            if old_f > 0 and new_f < 0.5 * old_f:
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
    """Canonical names on tonight's timecard (if present)."""
    from skills.adp_run_automation import shift_backend as sb  # noqa: PLC0415

    if not timecard_xlsx or not timecard_xlsx.exists():
        return []
    punches = sb.parse_xlsx(timecard_xlsx, employee_aliases=employee_aliases)
    return sorted({p["employee_name"] for p in punches if p.get("employee_name")})


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
    profile = load_store_profile("palmetto")
    bq_rows = [map_adp_wage_rate(r, profile) for r in fill]
    if dry_run:
        print(f"[pay_info] DRY: would MERGE {len(bq_rows)} rows ({len(changes)} rate change(s))")
        for r in fill:
            print(f"  {r['employee_name']}: ${r['wage_rate_dollars']}")
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
    """Breadcrumb + Slack warning. Never raises; never fails the nightly."""
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

    from skills.adp_run_automation.runner import (  # noqa: PLC0415
        _ensure_logged_in,
        launch_persistent,
    )

    with launch_persistent(portal="adp", headed=args.headed, slow_mo_ms=50) as (_ctx, page):
        _ensure_logged_in(page, store=args.store)
        dashboard_url = page.url
        rates, errors = scrape_pay_info_rates(page, names, dashboard_url=dashboard_url)
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
