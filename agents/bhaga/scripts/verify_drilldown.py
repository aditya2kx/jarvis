#!/usr/bin/env python3
"""BHAGA per-day drill-down for historical verification.

For each completed pay period, prints:
    1. Daily pool table (date | dow | pool | team_hours | workers | pool/hr)
       — the inputs to the allocator. Cross-checkable against Square dashboard
       (pool) and ADP timecards (team_hours).
    2. Per-employee day-by-day breakdown:
       date | dow | their_hours | day_pool | team_hrs | %_of_day | share_$
       — lets you see exactly which days contributed to each person's total.

Self-contained (does not import from verify_against_historical_payroll). Re-runs
the parses and the allocation each time, so it's safe to use even if the other
verification script's file state is unstable.

Usage:
    python3 -m agents.bhaga.scripts.verify_drilldown --store palmetto
    python3 -m agents.bhaga.scripts.verify_drilldown --store palmetto --only-period 2026-04-06
    python3 -m agents.bhaga.scripts.verify_drilldown --store palmetto --only-employee "Saldana, Daniel"
"""

from __future__ import annotations

import argparse
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
    return max(paths, key=lambda p: p.stat().st_mtime) if paths else None


def _money(cents: int) -> str:
    return f"${cents/100:>10,.2f}"


def discover_pay_periods(earnings: list[dict]) -> list[dict]:
    """Group earnings into canonical pay periods, merging off-by-1-day variants."""
    buckets: dict[tuple, dict] = {}
    for r in earnings:
        ps, pe = r.get("period_start"), r.get("period_end")
        if not ps or not pe:
            continue
        key = (ps, pe)
        b = buckets.setdefault(key, {"start": ps, "end": pe, "employees": set(), "check_dates": set()})
        b["employees"].add(r["employee_name"])
        b["check_dates"].add(r["check_date"])

    raw_periods = []
    for (ps, pe), b in sorted(buckets.items()):
        raw_periods.append({
            "start": ps, "end": pe,
            "employees": sorted(b["employees"]),
            "check_dates": sorted(b["check_dates"]),
        })

    canonical: list[dict] = []
    for p in raw_periods:
        ps_d = datetime.date.fromisoformat(p["start"])
        pe_d = datetime.date.fromisoformat(p["end"])
        merged = False
        for c in canonical:
            cs_d = datetime.date.fromisoformat(c["start"])
            ce_d = datetime.date.fromisoformat(c["end"])
            if ps_d == cs_d and abs((pe_d - ce_d).days) <= 2:
                c["variants"].append({"start": p["start"], "end": p["end"]})
                c["check_dates"] = sorted(set(c["check_dates"]) | set(p["check_dates"]))
                if pe_d > ce_d:
                    c["end"] = p["end"]
                merged = True
                break
        if not merged:
            canonical.append({
                "start": p["start"], "end": p["end"],
                "check_dates": p["check_dates"],
                "variants": [{"start": p["start"], "end": p["end"]}],
            })
    return canonical


def actual_cc_tips_by_period(earnings: list[dict]) -> dict[tuple, dict[str, int]]:
    """{(period_start, period_end): {employee_name: cents}} for 'Credit Card Tips Owed'."""
    out: dict[tuple, dict[str, int]] = {}
    for r in earnings:
        if r.get("description") != "Credit Card Tips Owed":
            continue
        key = (r["period_start"], r["period_end"])
        bucket = out.setdefault(key, {})
        cents = int(round(r["amount"] * 100))
        bucket[r["employee_name"]] = bucket.get(r["employee_name"], 0) + cents
    return out


def run_period(
    period: dict,
    *,
    shifts: list[dict],
    txns: list[dict],
    actuals: dict[tuple, dict[str, int]],
    excluded: set[str],
) -> dict:
    start, end = period["start"], period["end"]

    daily_hours: dict[tuple[str, str], float] = {}
    for s in shifts:
        if not (start <= s["date"] <= end):
            continue
        if s["employee_name"] in excluded:
            continue
        k = (s["employee_name"], s["date"])
        daily_hours[k] = daily_hours.get(k, 0.0) + s.get("total_hours", 0.0)

    daily_tips_cents: dict[str, int] = {}
    for t in txns:
        if not (start <= t["date_local"] <= end):
            continue
        daily_tips_cents[t["date_local"]] = (
            daily_tips_cents.get(t["date_local"], 0) + t.get("tip_cents", 0)
        )
    for d in list(daily_tips_cents.keys()):
        if daily_tips_cents[d] < 0:
            daily_tips_cents[d] = 0

    result = allocate(daily_tips_cents, daily_hours)

    actual_by_emp: dict[str, int] = {}
    for v in period.get("variants", [{"start": start, "end": end}]):
        for emp, c in actuals.get((v["start"], v["end"]), {}).items():
            actual_by_emp[emp] = actual_by_emp.get(emp, 0) + c

    all_dates = sorted({*daily_tips_cents.keys(), *{d for _, d in daily_hours.keys()}})
    daily_pool = []
    for d in all_dates:
        workers = [(e, h) for (e, dd), h in daily_hours.items() if dd == d and h > 0]
        team_hrs = sum(h for _, h in workers)
        pool_c = daily_tips_cents.get(d, 0)
        daily_pool.append({
            "date": d,
            "dow": datetime.date.fromisoformat(d).strftime("%a"),
            "pool_cents": pool_c,
            "team_hours": round(team_hrs, 2),
            "worker_count": len(workers),
            "pool_per_hour_cents": int(round(pool_c / team_hrs)) if team_hrs > 0 else 0,
        })

    return {
        "start": start, "end": end,
        "check_dates": period["check_dates"],
        "variants": period.get("variants", [{"start": start, "end": end}]),
        "daily_pool": daily_pool,
        "per_day_allocations": [
            {"date": p.date, "employee_name": p.employee, "hours": p.hours, "share_cents": p.share_cents}
            for p in result.per_day
        ],
        "per_period_ours": {p.employee: p.total_tip_cents for p in result.per_period},
        "per_period_hours": {p.employee: p.total_hours for p in result.per_period},
        "per_period_adp": actual_by_emp,
    }


def print_period(p: dict, *, only_employee: str | None = None) -> None:
    print()
    print("=" * 90)
    print(f"Pay period: {p['start']} -> {p['end']}  (check: {', '.join(p['check_dates'])})")
    if len(p["variants"]) > 1:
        print(f"  note: ADP has {len(p['variants'])} end-date variants in this period: "
              f"{[(v['start'], v['end']) for v in p['variants']]}")
    print("=" * 90)

    total_pool = sum(d["pool_cents"] for d in p["daily_pool"])
    total_hrs = sum(d["team_hours"] for d in p["daily_pool"])
    print()
    print(f"  DAILY POOL & HOURS  (cross-check Square dashboard for pool, ADP timecards for team_hours)")
    print(f"    {'Date':<11} {'DoW':<4} {'Pool':>10} {'Team hrs':>9} "
          f"{'Workers':>8} {'Pool/hr':>10}")
    print(f"    {'-'*11} {'-'*4} {'-'*10} {'-'*9} {'-'*8} {'-'*10}")
    for d in p["daily_pool"]:
        print(
            f"    {d['date']:<11} {d['dow']:<4} {_money(d['pool_cents'])} "
            f"{d['team_hours']:>9.2f} {d['worker_count']:>8} "
            f"{_money(d['pool_per_hour_cents'])}"
        )
    print(f"    {'-'*11} {'-'*4} {'-'*10} {'-'*9} {'-'*8} {'-'*10}")
    print(f"    {'TOTAL':<11} {'':<4} {_money(total_pool)} {total_hrs:>9.2f} {'':>8} {'':>10}")

    alloc_by_key: dict[tuple, dict] = {
        (a["employee_name"], a["date"]): a for a in p["per_day_allocations"]
    }
    daily_by_date = {d["date"]: d for d in p["daily_pool"]}

    employees = sorted(
        set(p["per_period_ours"]) | set(p["per_period_adp"])
    )
    if only_employee:
        employees = [e for e in employees if e == only_employee]
        if not employees:
            print(f"\n  (No employee named {only_employee!r} in this period.)")
            return

    for emp in employees:
        ours = p["per_period_ours"].get(emp, 0)
        adp = p["per_period_adp"].get(emp, 0)
        diff = ours - adp
        hrs = p["per_period_hours"].get(emp, 0.0)
        flag = ""
        if adp == 0 and ours > 0:
            flag = "  ** ADP PAID $0 **"
        elif ours == 0 and adp > 0:
            flag = "  ** WE COMPUTED $0 **"
        print()
        print(
            f"  --- {emp}  |  {hrs:.2f} hrs  |  our: {_money(ours)}  ADP: {_money(adp)}  "
            f"diff: {_money(diff)}{flag}"
        )
        emp_dates = sorted({a["date"] for a in p["per_day_allocations"] if a["employee_name"] == emp})
        if not emp_dates:
            print(f"      (no shifts; ADP paid {_money(adp)} -- worth investigating)")
            continue
        print(f"      {'Date':<11} {'DoW':<4} {'Hours':>6} {'Day pool':>10} "
              f"{'Team hrs':>9} {'% of day':>9} {'Share':>10}")
        print(f"      {'-'*11} {'-'*4} {'-'*6} {'-'*10} {'-'*9} {'-'*9} {'-'*10}")
        run_share = 0
        run_hrs = 0.0
        for d in emp_dates:
            a = alloc_by_key[(emp, d)]
            day = daily_by_date.get(d, {})
            pct = (100 * a["hours"] / day["team_hours"]) if day.get("team_hours") else 0.0
            run_share += a["share_cents"]
            run_hrs += a["hours"]
            print(
                f"      {d:<11} {datetime.date.fromisoformat(d).strftime('%a'):<4} "
                f"{a['hours']:>6.2f} {_money(day.get('pool_cents', 0))} "
                f"{day.get('team_hours', 0):>9.2f} {pct:>8.1f}% "
                f"{_money(a['share_cents'])}"
            )
        print(f"      {'-'*11} {'-'*4} {'-'*6} {'-'*10} {'-'*9} {'-'*9} {'-'*10}")
        print(f"      {'TOTAL':<11} {'':<4} {run_hrs:>6.2f} {'':>10} {'':>9} {'':>9} {_money(run_share)}")


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", required=True)
    cli.add_argument("--only-period", default=None,
        help="YYYY-MM-DD; restrict to the period whose start matches.")
    cli.add_argument("--only-employee", default=None,
        help="Canonical employee name (e.g. 'Saldana, Daniel') to drill down.")
    cli.add_argument("--include-partial", action="store_true",
        help="Include pay periods that start before Square data coverage.")
    args = cli.parse_args()

    profile = json.loads((STORE_PROFILE_DIR / f"{args.store}.json").read_text())
    aliases = profile["employees"]["aliases"]
    excluded = set(profile["employees"]["excluded_from_tip_pool_and_labor_pct"])
    shop_tz = profile["timezone"]["shop_tz"]

    timecard_xlsx = _newest("Timecard*.xlsx")
    earnings_xlsx = _newest("Earnings*.xlsx")
    txn_csv = _newest("transactions-*.csv")
    if not (timecard_xlsx and earnings_xlsx and txn_csv):
        print(f"MISSING: timecard={timecard_xlsx} earnings={earnings_xlsx} txn={txn_csv}")
        return 1

    print(f"# inputs: {timecard_xlsx.name}, {earnings_xlsx.name}, {txn_csv.name}")
    punches = shift_backend.parse_xlsx(timecard_xlsx, employee_aliases=aliases)
    shifts = shift_backend.aggregate_by_day(punches)
    earnings = compensation_backend.parse_xlsx(earnings_xlsx, employee_aliases=aliases)
    txns = transactions_backend.parse_csv(txn_csv, shop_tz=shop_tz)
    square_data_start = min((t["date_local"] for t in txns), default="9999-12-31")
    print(f"# parsed: {len(shifts)} shift-days, {len(earnings)} earning lines, {len(txns)} txns "
          f"(Square starts {square_data_start})")

    periods = discover_pay_periods(earnings)
    actuals = actual_cc_tips_by_period(earnings)

    for p in periods:
        if not args.include_partial and p["start"] < square_data_start:
            continue
        if args.only_period and p["start"] != args.only_period:
            continue
        result = run_period(
            p, shifts=shifts, txns=txns, actuals=actuals, excluded=excluded,
        )
        print_period(result, only_employee=args.only_employee)

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
