"""Solo-shift hour attribution from ADP punch intervals.

An employee is "solo" for the minutes when they are the only person punched in.
Those minutes earn a premium hourly rate (Issue #309: $16.25 instead of $15.25).

Pure module — no network, no IO, no clock — same contract as
skills/tip_pool_allocation (bhaga.mdc invariant 1). People get paid from this
output, so every threshold arrives as config rather than a literal.

Three rules that are easy to get backwards:

  * Occupancy counts every punched employee who is IN THE SHOP, including the
    manager. Being labor-excluded from the tip pool has nothing to do with
    whether a colleague is physically present. Eligibility is applied
    afterwards, separately.
  * A remote employee is punched in but not in the shop, so they neither occupy
    the floor for anyone else nor earn solo minutes themselves. Their hours
    still count in full — remote time lands in team_minutes. Treating a remote
    shift as floor coverage silently suppressed a premium the coworker had
    earned (live 2026-09-07: Tina worked 4.62h alone while the manager was
    remote, and was paid base for all of it).
  * Sub-threshold solo runs are folded into team minutes, never dropped. That is
    what keeps solo + team == total (bhaga.mdc invariant 2).
"""

from __future__ import annotations

from typing import NamedTuple

MINUTES_PER_DAY = 24 * 60
MINUTES_PER_HOUR = 60


class SoloConfig(NamedTuple):
    """Tunables from bhaga.store_config — never hardcode these."""

    min_block_minutes: int
    eligible_base_rate_cents: int
    premium_rate_cents: int
    effective_date: str  # ISO; punches before this earn no premium
    # (date, employee) shifts worked away from the shop. Default empty: an
    # unannotated shift is treated as on the floor, which is the safe reading of
    # silence for occupancy but does under-pay when an annotation is missed.
    remote_days: frozenset[tuple[str, str]] = frozenset()

    @property
    def premium_delta_cents(self) -> int:
        """Per-hour uplift in cents (e.g. $16.25 - $15.25 = 100)."""
        return self.premium_rate_cents - self.eligible_base_rate_cents

    def is_remote(self, date: str, employee: str) -> bool:
        """Was ``employee`` working away from the shop on ``date``?

        Compared case- and whitespace-insensitively: the config is hand-edited,
        and "krause, lindsay" must not quietly fail to match a punch row.
        """
        return (date.strip(), employee.strip().casefold()) in self.remote_days


def remote_day_key(date: str, employee: str) -> tuple[str, str]:
    """Normalise one remote-shift annotation for :attr:`SoloConfig.remote_days`."""
    return (date.strip(), employee.strip().casefold())


class SoloDay(NamedTuple):
    """One employee's solo/team split for one shop-local date."""

    date: str
    employee: str
    solo_minutes: int
    team_minutes: int
    total_minutes: int
    eligible: bool
    premium_cents: int
    # Subset of team_minutes worked away from the shop. Reported so the console
    # can show why an employee with hours has no solo time.
    remote_minutes: int = 0


def parse_hhmm(value: str | None) -> int | None:
    """``'16:01'`` -> 961 minutes past midnight. Malformed/empty -> ``None``."""
    text = (value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * MINUTES_PER_HOUR + minutes


def punch_interval(in_time: str | None, out_time: str | None) -> tuple[int, int] | None:
    """Half-open ``[start, end)`` minute interval for one punch.

    An out_time at or before in_time is treated as crossing midnight and gets
    +24h, matching the console's coverage model.
    """
    start = parse_hhmm(in_time)
    end = parse_hhmm(out_time)
    if start is None or end is None:
        return None
    if end <= start:
        end += MINUTES_PER_DAY
    return (start, end)


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sorted union of half-open intervals, touching/overlapping runs coalesced.

    Collapses one employee's split punches so a mid-shift break never
    double-counts toward their own total.
    """
    ordered = sorted(i for i in intervals if i[1] > i[0])
    if not ordered:
        return []
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _boundaries(by_employee: dict[str, list[tuple[int, int]]]) -> list[int]:
    """Sorted distinct minute marks where occupancy can change."""
    marks: set[int] = set()
    for intervals in by_employee.values():
        for start, end in intervals:
            marks.add(start)
            marks.add(end)
    return sorted(marks)


def solo_blocks(
    day_intervals: dict[str, list[tuple[int, int]]],
) -> dict[str, list[tuple[int, int]]]:
    """Per-employee maximal contiguous runs where occupancy is exactly 1.

    Sweeps the boundary marks rather than iterating minutes, so cost scales with
    punch count instead of clock length.
    """
    merged = {emp: merge_intervals(ivs) for emp, ivs in day_intervals.items()}
    merged = {emp: ivs for emp, ivs in merged.items() if ivs}
    if not merged:
        return {}

    marks = _boundaries(merged)
    runs: dict[str, list[tuple[int, int]]] = {}
    for i in range(len(marks) - 1):
        seg_start, seg_end = marks[i], marks[i + 1]
        if seg_end <= seg_start:
            continue
        present = [
            emp
            for emp, ivs in merged.items()
            if any(s <= seg_start and seg_end <= e for s, e in ivs)
        ]
        if len(present) != 1:
            continue
        emp = present[0]
        prior = runs.get(emp)
        if prior and prior[-1][1] == seg_start:
            prior[-1] = (prior[-1][0], seg_end)
        else:
            runs.setdefault(emp, []).append((seg_start, seg_end))
    return runs


def attribute_day(
    date: str,
    day_intervals: dict[str, list[tuple[int, int]]],
    base_rate_cents: dict[str, int],
    config: SoloConfig,
) -> list[SoloDay]:
    """One :class:`SoloDay` per employee present on ``date``.

    ``day_intervals`` is canonical-name -> punch intervals; callers must have
    already resolved aliases (bhaga.mdc invariant 10).

    Employees listed in ``config.remote_days`` for ``date`` still get a row with
    their full hours; those hours are all team, and all flagged remote.
    """
    merged = {emp: merge_intervals(ivs) for emp, ivs in day_intervals.items()}
    merged = {emp: ivs for emp, ivs in merged.items() if ivs}
    if not merged:
        return []

    # Occupancy is computed over the people actually in the shop. Remote staff
    # are excluded here and never appear in `runs`, so they cannot mask a
    # coworker's solo block nor collect one of their own.
    remote = {emp for emp in merged if config.is_remote(date, emp)}
    runs = solo_blocks({emp: ivs for emp, ivs in merged.items() if emp not in remote})
    in_effect = date >= config.effective_date

    out: list[SoloDay] = []
    for employee in sorted(merged):
        total = sum(end - start for start, end in merged[employee])
        solo = sum(
            end - start
            for start, end in runs.get(employee, [])
            if (end - start) >= config.min_block_minutes
        )
        rate = base_rate_cents.get(employee)
        eligible = in_effect and rate == config.eligible_base_rate_cents
        premium = (
            round(solo * config.premium_delta_cents / MINUTES_PER_HOUR)
            if eligible
            else 0
        )
        out.append(
            SoloDay(
                date=date,
                employee=employee,
                solo_minutes=solo,
                team_minutes=total - solo,
                total_minutes=total,
                eligible=eligible,
                premium_cents=premium,
                remote_minutes=total if employee in remote else 0,
            )
        )
    return out


def intervals_from_punches(
    punches: list[dict],
    *,
    aliases: dict[str, str] | None = None,
) -> dict[str, dict[str, list[tuple[int, int]]]]:
    """Group raw ``adp_punches`` rows into ``{date: {employee: intervals}}``.

    Rows with unparseable punch times are skipped rather than counted as zero —
    a missing punch is unknown coverage, not proof that nobody was there.
    """
    from skills.adp_run_automation.shift_backend import normalize_employee_name

    out: dict[str, dict[str, list[tuple[int, int]]]] = {}
    for row in punches:
        date = str(row.get("date") or "").strip()
        raw_name = row.get("employee_name") or row.get("canonical_name") or ""
        if not date or not raw_name:
            continue
        interval = punch_interval(row.get("in_time"), row.get("out_time"))
        if interval is None:
            continue
        employee = normalize_employee_name(str(raw_name), aliases)
        out.setdefault(date, {}).setdefault(employee, []).append(interval)
    return out
