#!/usr/bin/env python3
"""Effective-dated hourly rates (Issue #343).

``adp_wage_rates`` holds one current rate per employee, and two loaders write
it (pay_info nightly, earnings on Mon/Tue) — so it cannot say which rate
applied on a past shift date. ``adp_wage_rate_history`` does: one row per rate
change, read through ``vw_wage_rate_effective`` by every wage consumer.

A change row is appended only when a loaded rate differs from the latest
HISTORY row. Comparing against ``adp_wage_rates`` instead would mint a new
effective date every time the earnings load puts the last paycheck's rate back.
Both loaders feed it: pay_info (live setup rate, dated by ``resolve_effective_date``)
and earnings (paid rate, dated at its pay period's start, only if newer).

Operator override (runtime, no deploy)::

    python3 -m skills.adp_run_automation.wage_rate_history set \\
        --employee "Johnson, Dolce" --effective 2026-09-21 --rate 18.00
    python3 -m skills.adp_run_automation.wage_rate_history show --employee "Johnson, Dolce"
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

TABLE = "adp_wage_rate_history"
SEED_DATE = datetime.date(2000, 1, 1)
_CT = ZoneInfo("America/Chicago")
_PROFILES = (
    pathlib.Path(__file__).resolve().parents[2]
    / "agents" / "bhaga" / "knowledge-base" / "store-profiles"
)
_COLUMN_TYPES = {
    "effective_date": "DATE",
    "observed_at_utc": "TIMESTAMP",
    "wage_rate_dollars": "FLOAT64",
    "ot_rate_dollars": "FLOAT64",
}
_RATE_EPSILON = 0.005


def today_ct() -> datetime.date:
    return datetime.datetime.now(_CT).date()


def pay_period_anchor(store: str = "palmetto") -> datetime.date:
    profile = json.loads((_PROFILES / f"{store}.json").read_text())
    return datetime.date.fromisoformat(profile["adp_run"]["pay_periods_anchor_end_date"])


def pay_period_start(
    d: datetime.date, *, anchor_end: datetime.date, length_days: int = 14,
) -> datetime.date:
    """First day of the fixed-length pay period containing ``d``."""
    offset = (d - anchor_end).days
    periods_ahead = -(-offset // length_days)  # ceil division, also for negatives
    period_end = anchor_end + datetime.timedelta(days=periods_ahead * length_days)
    return period_end - datetime.timedelta(days=length_days - 1)


def resolve_effective_date(
    *,
    today: datetime.date,
    added_on: Optional[str],
    latest_effective: Optional[datetime.date],
    anchor_end: datetime.date,
    length_days: int = 14,
) -> tuple[datetime.date, str]:
    """Start date for a newly observed rate, and which rule produced it.

    ADP's "Added on" is used only when it lands in the current or previous pay
    period and after the last recorded change — on some profiles it is the hire
    date, which would back-date a raise over closed periods. Otherwise the rate
    starts on the first day of today's pay period: ADP RUN pays one rate per
    check, so a raise first seen mid-period covers that whole period.
    """
    current_start = pay_period_start(today, anchor_end=anchor_end, length_days=length_days)
    if added_on:
        try:
            added = datetime.date.fromisoformat(added_on)
        except ValueError:
            added = None
        earliest = current_start - datetime.timedelta(days=length_days)
        if (
            added is not None
            and earliest <= added <= today
            and (latest_effective is None or added > latest_effective)
        ):
            return added, "added_on"
    return current_start, "period_start"


def history_rows_for_changes(
    rates: list[dict],
    latest: dict[str, dict],
    *,
    today: datetime.date,
    anchor_end: datetime.date,
    observed_at: Optional[datetime.datetime] = None,
) -> list[dict]:
    """History rows for every scraped rate that differs from its latest history row.

    An employee with no history yet gets a ``SEED_DATE`` row: the one rate we
    know is the only rate we can apply to their past shifts.
    """
    observed_at = observed_at or datetime.datetime.now(datetime.timezone.utc)
    out: list[dict] = []
    for rec in rates:
        emp = rec.get("employee_id") or rec.get("employee_name")
        wage = rec.get("wage_rate_dollars")
        if not emp or wage is None:
            continue
        prev = latest.get(emp)
        if prev is None:
            effective, basis = SEED_DATE, "first_observation"
        else:
            prev_wage = prev.get("wage_rate_dollars")
            if prev_wage is not None and abs(float(prev_wage) - float(wage)) <= _RATE_EPSILON:
                continue
            effective, basis = resolve_effective_date(
                today=today,
                added_on=rec.get("added_on"),
                latest_effective=prev.get("effective_date"),
                anchor_end=anchor_end,
            )
        out.append({
            "employee_id": emp,
            "effective_date": effective,
            "wage_rate_dollars": float(wage),
            "ot_rate_dollars": rec.get("ot_rate_dollars"),
            "source": "pay_info",
            "observed_at_utc": observed_at,
            "note": f"basis={basis}",
        })
    return out


def history_rows_from_earnings(
    rates: list[dict],
    earnings: list[dict],
    latest: dict[str, dict],
    *,
    observed_at: Optional[datetime.datetime] = None,
) -> list[dict]:
    """History rows for paycheck rates newer than each employee's latest change.

    Employees whose pay_info scrape keeps failing only ever get rates this way.
    A paid rate takes effect at the start of the pay period on its newest check,
    and only when that period starts after the latest recorded change — an
    older check (e.g. the last period at the pre-raise rate) never undoes a
    newer pay_info change.
    """
    observed_at = observed_at or datetime.datetime.now(datetime.timezone.utc)
    newest_period: dict[str, tuple[str, str]] = {}
    for e in earnings:
        emp, check, start = e.get("employee_name"), str(e.get("check_date") or ""), str(e.get("period_start") or "")
        if not emp or not check or not start or e.get("description") != "Regular":
            continue
        if emp not in newest_period or check > newest_period[emp][0]:
            newest_period[emp] = (check, start)

    out: list[dict] = []
    for rec in rates:
        emp = rec.get("employee_id") or rec.get("employee_name")
        wage = rec.get("wage_rate_dollars")
        if not emp or wage is None or emp not in newest_period:
            continue
        prev = latest.get(emp)
        if prev is None:
            effective = SEED_DATE
        else:
            prev_wage = prev.get("wage_rate_dollars")
            if prev_wage is not None and abs(float(prev_wage) - float(wage)) <= _RATE_EPSILON:
                continue
            try:
                effective = datetime.date.fromisoformat(newest_period[emp][1][:10])
            except ValueError:
                continue
            if prev.get("effective_date") is not None and effective <= prev["effective_date"]:
                continue
        out.append({
            "employee_id": emp,
            "effective_date": effective,
            "wage_rate_dollars": float(wage),
            "ot_rate_dollars": rec.get("ot_rate_dollars"),
            "source": "earnings",
            "observed_at_utc": observed_at,
            "note": f"check_date={newest_period[emp][0]}",
        })
    return out


def record_earnings_changes(rates: list[dict], earnings: list[dict], *, dry_run: bool = False) -> int:
    """Append history rows for paycheck rates that moved past the latest change."""
    return write_history_rows(
        history_rows_from_earnings(rates, earnings, latest_history_bq()), dry_run=dry_run,
    )


def latest_history_bq() -> dict[str, dict]:
    """employee_id → its most recent history row."""
    from core.datastore import fq, read_query  # noqa: PLC0415

    rows = read_query(
        f"SELECT employee_id, effective_date, wage_rate_dollars FROM {fq(TABLE)} "
        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY employee_id ORDER BY effective_date DESC) = 1"
    )
    return {r["employee_id"]: r for r in rows}


def write_history_rows(rows: list[dict], *, dry_run: bool = False) -> int:
    from core.datastore import load_rows  # noqa: PLC0415

    for r in rows:
        print(
            f"[wage_rate_history] BREADCRUMB wage_rate_effective name={r['employee_id']} "
            f"rate={r['wage_rate_dollars']} effective={r['effective_date']} "
            f"source={r['source']} {r.get('note') or ''}".rstrip()
        )
    if dry_run or not rows:
        return 0
    return load_rows(
        TABLE, rows, merge_keys=["employee_id", "effective_date"],
        column_bq_types=_COLUMN_TYPES,
    )


def record_pay_info_changes(rates: list[dict], *, dry_run: bool = False, store: str = "palmetto") -> int:
    """Append history rows for tonight's pay_info rates that changed."""
    rows = history_rows_for_changes(
        rates, latest_history_bq(), today=today_ct(), anchor_end=pay_period_anchor(store),
    )
    return write_history_rows(rows, dry_run=dry_run)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="Record an operator rate change")
    s.add_argument("--store", default="palmetto")
    s.add_argument("--employee", required=True)
    s.add_argument("--effective", required=True, help="YYYY-MM-DD (America/Chicago)")
    s.add_argument("--rate", required=True, type=float)
    s.add_argument("--ot-rate", type=float, default=None)
    s.add_argument("--note", default="")
    s.add_argument("--dry-run", action="store_true")
    sh = sub.add_parser("show", help="Print an employee's rate history")
    sh.add_argument("--store", default="palmetto")
    sh.add_argument("--employee", required=True)
    args = ap.parse_args(argv)

    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")
    from agents.bhaga.scripts.model_inputs import normalize_input_name  # noqa: PLC0415

    employee = normalize_input_name(args.store, args.employee)
    if args.cmd == "show":
        from core.datastore import fq, read_query  # noqa: PLC0415

        rows = read_query(
            f"SELECT * FROM {fq('vw_wage_rate_effective')} "
            f"WHERE employee_id = '{employee.replace(chr(39), '')}' ORDER BY effective_from"
        )
        for r in rows:
            print(f"{r['effective_from']} → {r['effective_to']}  ${r['wage_rate_dollars']}  ({r['source']})")
        return 0

    row = {
        "employee_id": employee,
        "effective_date": datetime.date.fromisoformat(args.effective),
        "wage_rate_dollars": args.rate,
        "ot_rate_dollars": args.ot_rate,
        "source": "operator",
        "observed_at_utc": datetime.datetime.now(datetime.timezone.utc),
        "note": args.note or "operator override",
    }
    write_history_rows([row], dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
