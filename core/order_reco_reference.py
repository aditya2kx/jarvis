"""Pure-Python reference model of the order-recommendation water-fill (Issue #350).

Independent re-implementation of core/migrations/081_sp_refresh_order_reco.sql,
used to prove the procedure (and the legacy TVFs) produce the right numbers:
unit tests pin its behaviour on a golden fixture, and
scripts/order_reco_sandbox.py parity compares BigQuery output against it.

Rounding mirrors BigQuery ROUND (half away from zero), not Python's
banker's rounding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

_MAX_EXTRA_TUBS_PER_ITEM = 300
_WEIGHT_LBS = {"Açaí": 18}
_DEFAULT_WEIGHT_LBS = 20
_BOX_OVERHEAD_LBS = 50
_TUBS_PER_BOX = 40
_NOT_WATER_FILLED = {"Blade"}


@dataclass(frozen=True)
class ItemInput:
    item: str
    current_qty: float
    avg_daily_usage: float


@dataclass
class RecoInputs:
    as_of: date
    capacity: int
    dates: list[date]
    items: list[ItemInput]
    pins: dict[tuple[date, str], int] = field(default_factory=dict)
    actuals: dict[tuple[date, str], float] = field(default_factory=dict)


@dataclass(frozen=True)
class RecoRow:
    slot: int
    delivery_date: date
    item: str
    current_qty: float
    avg_daily_usage: float
    on_hand: float
    order_tubs: int
    weight: float | None
    after_restock: float
    days_left_after: float | None
    ord: int
    source: str


@dataclass
class RecoResult:
    rows: list[RecoRow]
    # slot -> True when the budget cutoff fell between equal scores, i.e. the
    # legacy TVFs (no tie-break) could legitimately pick either item.
    cutoff_tie: dict[int, bool]


def bq_round(x: float | None, digits: int = 0) -> float | None:
    if x is None:
        return None
    q = Decimal(1).scaleb(-digits)
    return float(Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP))


def _safe_div(a: float, b: float) -> float | None:
    return None if b == 0 else a / b


def _water_fill(on_hand: dict[str, float], avg: dict[str, float], budget: int,
                pinned: set[str]) -> tuple[dict[str, int], bool]:
    cands: list[tuple[float, str, int]] = []
    for item, oh in on_hand.items():
        if item in _NOT_WATER_FILLED or avg[item] <= 0 or item in pinned:
            continue
        for k in range(1, min(budget, _MAX_EXTRA_TUBS_PER_ITEM) + 1):
            cands.append(((oh + k - 1) / avg[item], item, k))
    cands.sort()
    chosen = cands[:budget]
    tie = 0 < budget < len(cands) and cands[budget - 1][0] == cands[budget][0]
    counts: dict[str, int] = {}
    for _, item, _k in chosen:
        counts[item] = counts.get(item, 0) + 1
    return counts, tie


def compute_reco(inputs: RecoInputs) -> RecoResult:
    rows: list[RecoRow] = []
    ties: dict[int, bool] = {}
    avg = {i.item: i.avg_daily_usage for i in inputs.items}
    prev: dict[str, tuple[float, int]] = {}  # item -> (rounded on_hand, int order)
    prev_date: date | None = None

    for slot, d in enumerate(sorted(inputs.dates), start=1):
        if slot == 1:
            days = (d - inputs.as_of).days
            arrival = {i.item: max(i.current_qty - days * i.avg_daily_usage, 0.0)
                       for i in inputs.items}
        else:
            days = (d - prev_date).days
            arrival = {i.item: max(prev[i.item][0] + prev[i.item][1] - days * i.avg_daily_usage, 0.0)
                       for i in inputs.items if i.item in prev}
        prev_date = d

        actuals = {item: q for (dd, item), q in inputs.actuals.items() if dd == d}
        pins = {item: q for (dd, item), q in inputs.pins.items() if dd == d}
        budget = max(math.floor(inputs.capacity - sum(arrival.values())) - sum(pins.values()), 0)
        est, ties[slot] = _water_fill(arrival, avg, budget, set(pins))

        slot_rows: list[RecoRow] = []
        for it in inputs.items:
            if it.item not in arrival:
                continue
            oh = arrival[it.item]
            if actuals:
                order, source = actuals.get(it.item, 0.0), "Actuals"
            elif it.item in pins:
                order, source = float(pins[it.item]), "Manual"
            else:
                order, source = float(est.get(it.item, 0)), "Estimated"
            weight = None if it.item in _NOT_WATER_FILLED else order * _WEIGHT_LBS.get(it.item, _DEFAULT_WEIGHT_LBS)
            slot_rows.append(RecoRow(
                slot=slot, delivery_date=d, item=it.item,
                current_qty=it.current_qty, avg_daily_usage=it.avg_daily_usage,
                on_hand=bq_round(oh, 2), order_tubs=int(bq_round(order)),
                weight=weight, after_restock=bq_round(oh + order, 2),
                days_left_after=bq_round(_safe_div(oh + order, it.avg_daily_usage), 1),
                ord=0, source=source,
            ))

        tot_order = (sum(actuals.get(r.item, 0.0) for r in slot_rows) if actuals
                     else float(sum(r.order_tubs for r in slot_rows)))
        weights = [r.weight for r in slot_rows if r.weight is not None]
        tot_after = sum(r.after_restock for r in slot_rows)
        tot_avg = sum(r.avg_daily_usage for r in slot_rows)
        slot_rows.append(RecoRow(
            slot=slot, delivery_date=d, item="TOTAL",
            current_qty=bq_round(sum(r.current_qty for r in slot_rows), 2),
            avg_daily_usage=bq_round(tot_avg, 2),
            on_hand=bq_round(sum(r.on_hand for r in slot_rows), 2),
            order_tubs=int(bq_round(tot_order)),
            weight=(bq_round(sum(weights) + _BOX_OVERHEAD_LBS * math.ceil(tot_order / _TUBS_PER_BOX))
                    if weights else None),
            after_restock=bq_round(tot_after, 2),
            days_left_after=bq_round(_safe_div(tot_after, tot_avg), 1),
            ord=1, source="Actuals" if actuals else "Estimated",
        ))
        rows.extend(slot_rows)
        prev = {r.item: (r.on_hand, r.order_tubs) for r in slot_rows if r.item != "TOTAL"}

    return RecoResult(rows=rows, cutoff_tie=ties)
