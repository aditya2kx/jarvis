#!/usr/bin/env python3
"""BHAGA historical verification: compare automated tip allocation against
actual ADP payouts on past paychecks.

For each completed pay period in the backfill window:
    1. Compute what BHAGA WOULD have allocated using pool-by-day fair share
       (the same logic the orchestrator will use going forward).
    2. Extract the ACTUAL "Credit Card Tips Owed" amounts ADP paid out that
       period (already sitting in the Earnings & Hours xlsx).
    3. Compare per employee, surface diffs, flag anomalies.

What this catches:
    * Bugs in our allocation math.
    * Periods where the historical manual method differed from pool-by-day.
    * Tips accidentally booked under the wrong ADP earnings code (e.g. card
      tips that landed in the "Cash tips" column in a card-only store).
    * Employees with hours but no tip payout (or vice-versa).

Assumes Palmetto-style setup:
    * Store does not take cash → ALL Square tips are card tips.
    * Manager (Lindsay Krause) is excluded from the tip pool.
    * Pay periods are biweekly. The script discovers actual period bounds
      from the data; it does not hardcode the schedule.

Usage:
    python3 -m agents.bhaga.scripts.verify_against_historical_payroll \\
        --store palmetto

    python3 -m agents.bhaga.scripts.verify_against_historical_payroll \\
        --store palmetto --write-json reports/verify.json
"""

from __future__ import annotations

import argparse
import collections
import datetime
import glob
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core.config_loader import project_dir
from skills.adp_run_automation import compensation_backend, shift_backend
from skills.square_tips import transactions_backend
from skills.tip_pool_allocation.adapter import allocate


PROJECT = pathlib.Path(project_dir())
DOWNLOADS = PROJECT / "extracted" / "downloads"
STORE_PROFILE_DIR = PROJECT / "agents" / "bhaga" / "knowledge-base" / "store-profiles"


def _newest(pattern: str) -> pathlib.Path | None:
    paths = [pathlib.Path(p) for p in glob.glob(str(DOWNLOADS / pattern))]
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)


def load_store_profile(store: str) -> dict:
    return json.loads((STORE_PROFILE_DIR / f"{store}.json").read_text())


# ── Pay period discovery ──────────────────────────────────────────


def discover_pay_periods(earnings: list[dict]) -> list[dict]:
    """Find (start, end, check_date) tuples covering the data. Group across employees.

    Returns sorted list of dicts: {start, end, check_dates: [...], employees: [...]}
    """
    # Bucket by (period_start, period_end)
    buckets: dict[tuple, dict] = {}
    for r in earnings:
        ps, pe = r.get("period_start"), r.get("period_end")
        if not ps or not pe:
            continue
        key = (ps, pe)
        b = buckets.setdefault(key, {
            "start": ps,
            "end": pe,
            "check_dates": set(),
            "employees": set(),
        })
        b["check_dates"].add(r["check_date"])
        b["employees"].add(r["employee_name"])

    out = []
    for (ps, pe), b in sorted(buckets.items()):
        b["check_dates"] = sorted(b["check_dates"])
        b["employees"] = sorted(b["employees"])
        out.append(b)
    return out


def normalize_pay_periods(periods: list[dict]) -> list[dict]:
    """Some employees may have slightly different end dates (off by 1 day due to
    manual edits in ADP). Group periods whose ranges overlap >= 90% into one
    canonical period with the modal start/end. Print details so user can see.
    """
    if not periods:
        return []
    canonical: list[dict] = []
    for p in periods:
        ps_d = datetime.date.fromisoformat(p["start"])
        pe_d = datetime.date.fromisoformat(p["end"])
        merged = False
        for c in canonical:
            cs_d = datetime.date.fromisoformat(c["start"])
            ce_d = datetime.date.fromisoformat(c["end"])
            # If new period's start matches AND end is within 2 days, merge.
            if ps_d == cs_d and abs((pe_d - ce_d).days) <= 2:
                c["variants"].append({
                    "start": p["start"], "end": p["end"],
                    "employees": p["employees"],
                })
                c["employees"] = sorted(set(c["employees"]) | set(p["employees"]))
                c["check_dates"] = sorted(set(c["check_dates"]) | set(p["check_dates"]))
                # Keep the later end as canonical (covers all employees).
                if pe_d > ce_d:
                    c["end"] = p["end"]
                merged = True
                break
        if not merged:
            canonical.append({
                "start": p["start"],
                "end": p["end"],
                "check_dates": p["check_dates"],
                "employees": p["employees"],
                "variants": [{"start": p["start"], "end": p["end"], "employees": p["employees"]}],
            })
    return canonical


# ── Build per-day hours and per-day tips for a window ─────────────


def build_daily_hours(
    shifts: list[dict],
    start: str,
    end: str,
    excluded: set[str],
) -> dict[tuple[str, str], float]:
    """Return {(employee_name, date_iso) -> hours} for window, excluding excluded employees."""
    out: dict[tuple[str, str], float] = {}
    for s in shifts:
        d = s["date"]
        if not (start <= d <= end):
            continue
        if s["employee_name"] in excluded:
            continue
        key = (s["employee_name"], d)
        out[key] = out.get(key, 0.0) + s.get("total_hours", 0.0)
    return out


def build_daily_tips_cents(
    txns: list[dict],
    start: str,
    end: str,
) -> dict[str, int]:
    """Sum tip_cents by shop-local date for the window. Refunds reduce the pool."""
    out: dict[str, int] = {}
    for t in txns:
        d = t["date_local"]
        if not (start <= d <= end):
            continue
        out[d] = out.get(d, 0) + t.get("tip_cents", 0)
    # Floor at 0 -- a day with net-negative tips (more refunded than collected)
    # would mean no pool to distribute. (Doesn't occur in Palmetto data.)
    for d in list(out.keys()):
        if out[d] < 0:
            out[d] = 0
    return out


# ── Actuals from ADP earnings ─────────────────────────────────────


def actual_cc_tips_by_period(earnings: list[dict]) -> dict[tuple[str, str], dict[str, int]]:
    """{(period_start, period_end): {employee_name: cc_tips_owed_cents}}.

    Sums across reversal triplets so net values are returned.
    """
    out: dict[tuple, dict[str, int]] = {}
    for r in earnings:
        if r.get("description") != "Credit Card Tips Owed":
            continue
        key = (r["period_start"], r["period_end"])
        emp = r["employee_name"]
        bucket = out.setdefault(key, {})
        # ADP amount is in dollars; convert to cents (preserve sign for reversals).
        cents = int(round(r["amount"] * 100))
        bucket[emp] = bucket.get(emp, 0) + cents
    return out


def cash_tip_anomalies(earnings: list[dict]) -> list[dict]:
    """Flag any non-zero net 'Cash tips' or 'Misc reimbursement non-taxable' lines.
    Palmetto doesn't take cash, so these are likely misclassified card tips."""
    by_emp_period_desc: dict[tuple, int] = collections.defaultdict(int)
    for r in earnings:
        desc = r.get("description", "")
        if desc not in ("Cash tips", "Misc reimbursement non-taxable"):
            continue
        key = (r["period_start"], r["period_end"], r["employee_name"], desc)
        by_emp_period_desc[key] += int(round(r["amount"] * 100))

    flags = []
    for (ps, pe, emp, desc), cents in sorted(by_emp_period_desc.items()):
        if cents != 0:
            flags.append({
                "period_start": ps,
                "period_end": pe,
                "employee_name": emp,
                "description": desc,
                "amount_cents": cents,
                "amount_dollars": cents / 100.0,
            })
    return flags


# ── Verification per pay period ───────────────────────────────────


def verify_period(
    period: dict,
    *,
    shifts: list[dict],
    txns: list[dict],
    actuals: dict[tuple, dict[str, int]],
    excluded: set[str],
    square_data_start: str,
) -> dict:
    """Run allocation for one pay period and compare against ADP actuals."""
    start, end = period["start"], period["end"]

    # Data coverage check
    coverage_warning = None
    if start < square_data_start:
        coverage_warning = (
            f"Square data starts {square_data_start}; period starts {start}. "
            f"Pool for days before Square coverage will be 0 -> our allocation under-counts."
        )

    daily_hours = build_daily_hours(shifts, start, end, excluded)
    daily_tips = build_daily_tips_cents(txns, start, end)

    result = allocate(daily_tips, daily_hours)

    our_by_emp = {p.employee: p.total_tip_cents for p in result.per_period}
    hours_by_emp = {p.employee: p.total_hours for p in result.per_period}
    # Always merge across ALL recorded variants of this period (the canonical
    # (start,end) is itself in the variants list). Some employees have a
    # slightly different period end date in ADP due to mid-cycle edits, and
    # those rows live under a separate key in `actuals` -- we must sum them all.
    actual_by_emp: dict[str, int] = {}
    variant_keys = {(v["start"], v["end"]) for v in period.get("variants", [])}
    variant_keys.add((start, end))
    for key in variant_keys:
        for emp, c in actuals.get(key, {}).items():
            actual_by_emp[emp] = actual_by_emp.get(emp, 0) + c

    all_employees = sorted(set(our_by_emp) | set(actual_by_emp))

    per_employee = []
    for emp in all_employees:
        ours = our_by_emp.get(emp, 0)
        actual = actual_by_emp.get(emp, 0)
        diff = ours - actual
        hrs = hours_by_emp.get(emp, 0.0)
        per_employee.append({
            "employee_name": emp,
            "hours": round(hrs, 2),
            "our_cents": ours,
            "adp_cents": actual,
            "diff_cents": diff,
            "diff_dollars": diff / 100.0,
            "diff_pct_of_adp": (
                round(100 * diff / actual, 1) if actual else None
            ),
            "in_our_calc_only": ours > 0 and actual == 0,
            "in_adp_only": actual > 0 and ours == 0,
        })

    total_ours = sum(p["our_cents"] for p in per_employee)
    total_actual = sum(p["adp_cents"] for p in per_employee)
    total_tip_pool = sum(daily_tips.values())  # Square card tips for period

    return {
        "start": start,
        "end": end,
        "check_dates": period["check_dates"],
        "coverage_warning": coverage_warning,
        "square_card_tips_cents": total_tip_pool,
        "our_allocation_total_cents": total_ours,
        "adp_paid_cc_tips_total_cents": total_actual,
        "square_vs_adp_diff_cents": total_tip_pool - total_actual,
        "our_vs_adp_diff_cents": total_ours - total_actual,
        "days_with_pool": sum(1 for v in daily_tips.values() if v > 0),
        "days_with_zero_pool_but_hours": sum(
            1 for d in {h[1] for h in daily_hours.keys()}
            if daily_tips.get(d, 0) == 0
        ),
        "allocator_flags": [f.__dict__ for f in result.flags],
        "per_employee": per_employee,
        "variants_merged": period.get("variants", [{"start": start, "end": end}]),
    }


# ── Pretty printing ───────────────────────────────────────────────


def _money(cents: int) -> str:
    return f"${cents/100:>10,.2f}"


def _maybe_pct(p: float | None) -> str:
    if p is None:
        return "    n/a"
    sign = "+" if p > 0 else ""
    return f"{sign}{p:>5.1f}%"


def print_period(p: dict) -> None:
    print("=" * 90)
    print(f"Pay period: {p['start']} → {p['end']}  (check: {', '.join(p['check_dates'])})")
    if p["coverage_warning"]:
        print(f"  ⚠ coverage: {p['coverage_warning']}")
    if len(p["variants_merged"]) > 1:
        print(f"  note: merged {len(p['variants_merged'])} variants (ADP recorded slightly different end dates per employee):")
        for v in p["variants_merged"]:
            print(f"        {v['start']} → {v['end']}  ({len(v['employees'])} employees)")
    print(f"  Square card tips collected: {_money(p['square_card_tips_cents'])}")
    print(f"  ADP CC Tips Owed paid:      {_money(p['adp_paid_cc_tips_total_cents'])}  "
          f"(Square - ADP = {_money(p['square_vs_adp_diff_cents'])})")
    print(f"  Our allocation total:       {_money(p['our_allocation_total_cents'])}  "
          f"(Our - ADP = {_money(p['our_vs_adp_diff_cents'])})")
    print(f"  Days in period with pool>0: {p['days_with_pool']}; "
          f"days w/ hours but $0 pool: {p['days_with_zero_pool_but_hours']}")
    if p["allocator_flags"]:
        print(f"  Allocator flags:")
        for f in p["allocator_flags"]:
            print(f"    - {f['date']} {f['issue']} {f}")
    print()
    print(f"  {'Employee':<22} {'Hours':>7} {'Our calc':>12} {'ADP actual':>12} "
          f"{'Diff':>10} {'Δ%':>8}  Notes")
    print(f"  {'-'*22} {'-'*7} {'-'*12} {'-'*12} {'-'*10} {'-'*8}  {'-'*30}")
    for e in p["per_employee"]:
        notes = []
        if e["in_our_calc_only"]:
            notes.append("we say owed, ADP paid $0")
        if e["in_adp_only"]:
            notes.append("ADP paid but we computed $0")
        if abs(e["diff_cents"]) >= 100 and not (e["in_our_calc_only"] or e["in_adp_only"]):
            notes.append("≥ $1 diff")
        print(
            f"  {e['employee_name']:<22} {e['hours']:>7.2f} "
            f"{_money(e['our_cents'])} {_money(e['adp_cents'])} "
            f"{_money(e['diff_cents'])} {_maybe_pct(e['diff_pct_of_adp']):>8}  "
            f"{'; '.join(notes)}"
        )
    print()


def print_anomalies(flags: list[dict]) -> None:
    if not flags:
        print("No 'Cash tips' or 'Misc reimbursement non-taxable' lines with non-zero net amounts found.")
        print("(For a card-only store, this is the expected state.)")
        return

    print("=" * 90)
    print(f"ANOMALY SCAN: non-zero 'Cash tips' / 'Misc reimbursement' lines")
    print(f"(In a card-only store these are typically misclassified card tips.)")
    print()
    print(f"  {'Pay period':<24} {'Employee':<22} {'Description':<32} {'Amount':>10}")
    print(f"  {'-'*24} {'-'*22} {'-'*32} {'-'*10}")
    for f in flags:
        period = f"{f['period_start']} → {f['period_end']}"
        print(f"  {period:<24} {f['employee_name']:<22} {f['description']:<32} "
              f"{_money(f['amount_cents'])}")
    print()


def print_grand_total(report: dict) -> None:
    periods = report["periods"]
    if not periods:
        return
    print("=" * 90)
    print("ACROSS ALL VERIFIED PERIODS")
    sq = sum(p["square_card_tips_cents"] for p in periods)
    adp = sum(p["adp_paid_cc_tips_total_cents"] for p in periods)
    ours = sum(p["our_allocation_total_cents"] for p in periods)
    print(f"  Square card tips collected: {_money(sq)}")
    print(f"  ADP CC Tips Owed paid:      {_money(adp)}  "
          f"(Square - ADP = {_money(sq - adp)})")
    print(f"  Our allocation total:       {_money(ours)}  "
          f"(Our - ADP = {_money(ours - adp)})")
    print()


# ── Main ──────────────────────────────────────────────────────────


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", required=True)
    cli.add_argument("--write-json", default=None,
        help="Path to write the structured report (defaults to "
             "agents/bhaga/knowledge-base/historical-verification/{store}-{date}.json).")
    cli.add_argument("--include-partial", action="store_true",
        help="Include pay periods whose start is before Square data coverage (under-counts).")
    args = cli.parse_args()

    profile = load_store_profile(args.store)
    aliases = profile["employees"]["aliases"]
    excluded = set(profile["employees"]["excluded_from_tip_pool_and_labor_pct"])
    shop_tz = profile["timezone"]["shop_tz"]

    # Parse the source files (don't re-scrape — use cached downloads).
    timecard_xlsx = _newest("Timecard*.xlsx")
    earnings_xlsx = _newest("Earnings*.xlsx")
    txn_csv = _newest("transactions-*.csv")
    if not (timecard_xlsx and earnings_xlsx and txn_csv):
        print("MISSING input file(s):")
        print(f"  timecard: {timecard_xlsx}")
        print(f"  earnings: {earnings_xlsx}")
        print(f"  txn csv:  {txn_csv}")
        return 1

    print(f"# parsing {timecard_xlsx.name}, {earnings_xlsx.name}, {txn_csv.name}")
    punches = shift_backend.parse_xlsx(timecard_xlsx, employee_aliases=aliases)
    shifts = shift_backend.aggregate_by_day(punches)
    earnings = compensation_backend.parse_xlsx(earnings_xlsx, employee_aliases=aliases)
    txns = transactions_backend.parse_csv(txn_csv, shop_tz=shop_tz)

    square_dates = sorted({t["date_local"] for t in txns})
    square_data_start = square_dates[0] if square_dates else "9999-12-31"

    print(f"  shifts: {len(shifts)} shift-days")
    print(f"  earnings: {len(earnings)} lines")
    print(f"  txns: {len(txns)} across {len(square_dates)} days "
          f"({square_data_start} → {square_dates[-1] if square_dates else 'n/a'})")
    print()

    raw_periods = discover_pay_periods(earnings)
    periods = normalize_pay_periods(raw_periods)
    actuals = actual_cc_tips_by_period(earnings)

    print(f"# discovered {len(periods)} pay period(s):")
    for p in periods:
        print(f"   - {p['start']} → {p['end']} (check {','.join(p['check_dates'])}, "
              f"{len(p['employees'])} employees)")
    print()

    verified = []
    for p in periods:
        if not args.include_partial and p["start"] < square_data_start:
            print(f"# SKIP period {p['start']}→{p['end']}: starts before Square data ({square_data_start}). "
                  f"Pass --include-partial to include.")
            continue
        if args.only_period and p["start"] != args.only_period:
            continue
        v = verify_period(
            p, shifts=shifts, txns=txns,
            actuals=actuals, excluded=excluded,
            square_data_start=square_data_start,
        )
        verified.append(v)

    anomalies = cash_tip_anomalies(earnings)

    report = {
        "store": args.store,
        "generated_at_utc": (
            datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ),
        "square_data_start": square_data_start,
        "square_data_end": square_dates[-1] if square_dates else None,
        "excluded_from_pool": sorted(excluded),
        "periods": verified,
        "cash_classification_anomalies": anomalies,
        "summary_totals_cents": {
            "square_card_tips": sum(p["square_card_tips_cents"] for p in verified),
            "adp_cc_tips_paid": sum(p["adp_paid_cc_tips_total_cents"] for p in verified),
            "our_allocation": sum(p["our_allocation_total_cents"] for p in verified),
        },
    }

    print()
    print("#" * 90)
    print("#  PER-PERIOD VERIFICATION")
    print("#" * 90)
    print()
    for v in verified:
        print_period(v)
        if args.drilldown:
            print_period_drilldown(v, min_diff_dollars=args.drilldown_min_diff)
    print_grand_total(report)
    print_anomalies(anomalies)

    out_path = args.write_json or str(
        PROJECT / "agents" / "bhaga" / "knowledge-base"
        / "historical-verification"
        / f"{args.store}-{datetime.date.today().isoformat()}.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Structured report written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
