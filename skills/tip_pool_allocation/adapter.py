#!/usr/bin/env python3
"""Pool-by-day tip allocation — pure function with cent-exact rounding.

THE rule (enshrined in `.cursor/rules/bhaga.md` rule #5):
    For each individual date:
        employee_share_for_date =
            (employee_hours_on_date / total_team_hours_on_date) * tip_pool_for_date
    Then SUM across the period for the per-employee period total.

    NEVER pool the whole period's tips against the whole period's hours.
    That under-rewards employees who worked the high-tip days.

Rounding (rule #11):
    Distribute residual cents deterministically via the LARGEST-REMAINDER
    method so the total of per-employee shares on a date equals that date's
    tip pool exactly. Ties broken lexicographically by employee id for
    determinism — the same inputs must always produce the same outputs.

Edge cases (all flagged, none raise):
    - Tips on a day where nobody logged hours → flagged; no allocation
    - Hours on a day with zero tips → rows emitted with share_cents=0
    - Employee with 0 hours on a day (but present in some other day) →
      row omitted for that date (shares allocate only to workers)
    - Empty inputs → empty result, no flags
    - Non-integer `tip_cents` → ValueError (caller bug)
    - Negative inputs → ValueError (caller bug)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional


@dataclass
class PerDayShare:
    date: str              # ISO date "YYYY-MM-DD"
    employee: str          # stable id (ADP file number or equivalent)
    hours: float
    share_cents: int       # always >= 0, sum across employees for a date == pool


@dataclass
class PerPeriodTotal:
    employee: str
    total_hours: float
    total_tip_cents: int


@dataclass
class Flag:
    date: str
    issue: str              # "tips_with_no_hours" | "hours_with_no_tips" | "zero_pool_zero_hours"
    tip_cents: Optional[int] = None
    total_hours: Optional[float] = None
    employees: list[str] = field(default_factory=list)


@dataclass
class AllocationResult:
    per_day: list[PerDayShare]
    per_period: list[PerPeriodTotal]
    flags: list[Flag]

    def as_dict(self) -> dict:
        """JSON-friendly dict, useful for sheet population and debugging."""
        return {
            "per_day": [p.__dict__ for p in self.per_day],
            "per_period": [p.__dict__ for p in self.per_period],
            "flags": [f.__dict__ for f in self.flags],
        }


# ── Core allocation logic ────────────────────────────────────────


def _validate(daily_tips: Mapping[str, int], daily_hours: Mapping[tuple[str, str], float]) -> None:
    for date, cents in daily_tips.items():
        if not isinstance(cents, int):
            raise ValueError(
                f"tip_cents for {date!r} must be int (got {type(cents).__name__}); "
                f"currency math is integer math."
            )
        if cents < 0:
            raise ValueError(f"tip_cents for {date!r} is negative: {cents}")
    for (emp, date), hrs in daily_hours.items():
        if hrs < 0:
            raise ValueError(f"hours for ({emp!r}, {date!r}) is negative: {hrs}")


def _largest_remainder_distribute(pool_cents: int, weights: list[tuple[str, float]]) -> dict[str, int]:
    """Distribute pool_cents across keys in `weights` so the total equals pool_cents exactly.

    `weights` is a list of (key, weight) pairs. Weights need not sum to 1 — they
    are normalized. Rounding follows the largest-remainder method: each key
    gets floor(ideal_share), then remaining cents are awarded one-by-one to
    keys in descending order of fractional remainder. Ties broken by key
    (lexicographic, ascending) for determinism.

    Returns dict[key -> cents]. If total weight is 0, returns {key: 0} for each.
    """
    if pool_cents == 0 or not weights:
        return {k: 0 for k, _ in weights}
    total_weight = sum(w for _, w in weights)
    if total_weight <= 0:
        return {k: 0 for k, _ in weights}

    ideal = {k: (w / total_weight) * pool_cents for k, w in weights}
    floors = {k: int(v) for k, v in ideal.items()}      # floor because all ideal >= 0
    remainders = {k: ideal[k] - floors[k] for k in ideal}

    distributed = sum(floors.values())
    leftover = pool_cents - distributed  # always >= 0 and < len(weights)

    # Rank by (-remainder, key) so ties resolve by lexicographic key order.
    rank_order = sorted(ideal.keys(), key=lambda k: (-remainders[k], k))
    result = dict(floors)
    for k in rank_order[:leftover]:
        result[k] += 1

    # Sanity check — this would be a bug in the allocator, not a caller issue.
    assert sum(result.values()) == pool_cents, (
        f"largest-remainder distribution sum {sum(result.values())} != pool {pool_cents}"
    )
    return result


def allocate(
    daily_tips: Mapping[str, int],
    daily_hours: Mapping[tuple[str, str], float],
) -> AllocationResult:
    """Pool-by-day fair share allocation.

    Args:
        daily_tips: {date_iso -> tip_pool_cents}. Integer cents only.
        daily_hours: {(employee_id, date_iso) -> hours}. Floats allowed.
                     Employees with 0 hours on a date need NOT be present —
                     missing entries are treated the same as 0.

    Returns:
        AllocationResult with:
          - per_day: one row per (date, employee) where employee > 0 hours
          - per_period: one row per employee with summed hours and tip cents
          - flags: anomalies for the caller to surface on Slack / in sheet Notes

    Guarantees:
        - For every date in daily_tips with at least one positive-hours worker,
          sum of per_day.share_cents for that date == daily_tips[date] (exactly,
          no lost or extra cents).
        - For every employee, sum of per_day.share_cents across dates ==
          per_period.total_tip_cents.
    """
    _validate(daily_tips, daily_hours)

    # Organize hours by date: date_iso -> [(employee, hours), ...] for hours > 0
    by_date: dict[str, list[tuple[str, float]]] = {}
    employees: set[str] = set()
    all_dates: set[str] = set(daily_tips.keys())
    for (emp, date), hrs in daily_hours.items():
        all_dates.add(date)
        employees.add(emp)
        if hrs > 0:
            by_date.setdefault(date, []).append((emp, hrs))

    per_day: list[PerDayShare] = []
    flags: list[Flag] = []

    for date in sorted(all_dates):
        pool = daily_tips.get(date, 0)
        workers = sorted(by_date.get(date, []))  # stable order by (emp, hrs) → emp alpha

        if pool > 0 and not workers:
            flags.append(Flag(
                date=date,
                issue="tips_with_no_hours",
                tip_cents=pool,
            ))
            continue

        if pool == 0 and workers:
            # Emit zero-share rows so caller sees everyone present, but flag as noteworthy.
            flags.append(Flag(
                date=date,
                issue="hours_with_no_tips",
                tip_cents=0,
                total_hours=sum(h for _, h in workers),
                employees=sorted({e for e, _ in workers}),
            ))
            for emp, hrs in workers:
                per_day.append(PerDayShare(date=date, employee=emp, hours=hrs, share_cents=0))
            continue

        if pool == 0 and not workers:
            # Nothing happened that day. Not flag-worthy unless caller expected activity.
            continue

        # Normal case: distribute `pool` cents across workers weighted by hours.
        distribution = _largest_remainder_distribute(pool, [(emp, hrs) for emp, hrs in workers])
        for emp, hrs in workers:
            per_day.append(PerDayShare(
                date=date,
                employee=emp,
                hours=hrs,
                share_cents=distribution[emp],
            ))

    # Per-period totals
    totals: dict[str, tuple[float, int]] = {emp: (0.0, 0) for emp in employees}
    # Re-walk daily_hours for totals (includes zero-tip days so hours are counted)
    for (emp, date), hrs in daily_hours.items():
        if hrs > 0:
            h, t = totals[emp]
            totals[emp] = (h + hrs, t)
    for row in per_day:
        h, t = totals[row.employee]
        totals[row.employee] = (h, t + row.share_cents)

    per_period = [
        PerPeriodTotal(employee=emp, total_hours=h, total_tip_cents=t)
        for emp, (h, t) in sorted(totals.items())
    ]

    return AllocationResult(per_day=per_day, per_period=per_period, flags=flags)


# ── CLI (handy for debugging / manual verification) ─────────────


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Pool-by-day tip allocator")
    parser.add_argument(
        "--input-json",
        required=True,
        help='JSON object: {"daily_tips": {"2026-04-01": 12450, ...}, '
             '"daily_hours": {"emp_001|2026-04-01": 7.5, ...}} '
             '(use "|" as delimiter between emp and date since JSON keys can\'t be tuples)',
    )
    args = parser.parse_args()

    data = json.loads(args.input_json)
    tips = data["daily_tips"]
    hours_raw = data["daily_hours"]
    hours = {tuple(k.split("|", 1)): float(v) for k, v in hours_raw.items()}

    result = allocate(tips, hours)
    print(json.dumps(result.as_dict(), indent=2))
