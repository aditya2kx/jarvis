"""Keep N upcoming restock schedule dates filled from a delivery weekday.

Tunables (``bhaga.store_config``, unset = automation off):
  - ``restock_delivery_weekday`` — e.g. ``friday``
  - ``restock_horizon_dates`` — integer floor of live dates (default 2 when weekday set)

Only **adds** register-only schedule rows. Never deletes, moves, or touches Actuals /
tub pins. Mirrored by apps/operator-console/lib/inventory/restockHorizon.ts — keep
both in lock-step.

Every delivery weekday is a *slot*. A slot is not added when:
  - it is already scheduled;
  - the operator removed it (latest ``inventory_edit_log`` row for that date is a
    ``restock_schedule`` clear — a cancellation, or the source of a Move);
  - an off-weekday live date stands in for it. An off-weekday date next to a
    removed slot is that slot's delayed/early delivery (Move = clear + set), so it
    stands in for nothing else; otherwise it stands in for the nearest slot
    (Thursday before a holiday Friday, Monday after a late Friday).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Iterable

logger = logging.getLogger(__name__)

AUTOMATION_BY = "restock-horizon"
WEEKDAY_KEY = "restock_delivery_weekday"
HORIZON_KEY = "restock_horizon_dates"
DEFAULT_HORIZON = 2

WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def plan_horizon_additions(
    *,
    today: dt.date,
    weekday: str | None,
    horizon: int,
    scheduled: Iterable[dt.date],
    today_closed: bool = False,
    removed: Iterable[dt.date] = (),
) -> list[dt.date]:
    """Return calendar dates to register so live schedule reaches *horizon*.

    Live = scheduled dates from the first open day: today, or tomorrow once
    today's base closing is in (same rule as vw_order_reco_next_dates). Empty
    when *weekday* is unset/unknown/``off``, *horizon* < 1, or enough live
    dates exist. Slot rules in the module docstring.
    """
    if not weekday:
        return []
    target = WEEKDAYS.get(weekday.strip().lower())
    if target is None or horizon < 1:
        return []

    start = today + dt.timedelta(days=1) if today_closed else today
    live = sorted({d for d in scheduled if d >= start})
    if len(live) >= horizon:
        return []

    removed_slots = set(removed)
    taken = set(removed_slots) | {d for d in live if d.weekday() == target}
    move_sources: set[dt.date] = set()
    for d in live:
        if d.weekday() == target:
            continue
        prev = d - dt.timedelta(days=(d.weekday() - target) % 7)
        nxt = prev + dt.timedelta(days=7)
        near, far = (prev, nxt) if d - prev <= nxt - d else (nxt, prev)
        source = next((s for s in (near, far) if s in removed_slots and s not in move_sources), None)
        if source is not None:
            move_sources.add(source)
            continue
        free = next((s for s in (near, far) if s not in taken), None)
        if free is not None:
            taken.add(free)

    need = horizon - len(live)
    added: list[dt.date] = []
    slot = start + dt.timedelta(days=(target - start.weekday()) % 7)
    for _ in range(53):
        if len(added) >= need:
            break
        if slot not in taken:
            added.append(slot)
        slot += dt.timedelta(days=7)
    return added


def _parse_horizon(raw: str | None) -> int:
    if raw is None or not str(raw).strip():
        return DEFAULT_HORIZON
    try:
        return int(str(raw).strip())
    except ValueError:
        return DEFAULT_HORIZON


def _to_date(v: object) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


INPUTS_SQL = """
WITH today AS (SELECT CURRENT_DATE('America/Chicago') AS d),
removed AS (
  SELECT delivery_date, action FROM {edit_log}
  WHERE store = @store AND entity = 'restock_schedule'
    AND delivery_date >= DATE_SUB((SELECT d FROM today), INTERVAL 7 DAY)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY delivery_date ORDER BY edited_at DESC) = 1
)
SELECT
  (SELECT d FROM today) AS today,
  EXISTS (
    SELECT 1 FROM {closing} c
    WHERE c.store = @store AND c.category = 'base' AND c.submitted_date = (SELECT d FROM today)
  ) AS today_closed,
  ARRAY(
    SELECT DISTINCT delivery_date FROM {schedule}
    WHERE store = @store AND delivery_date >= (SELECT d FROM today)
  ) AS scheduled,
  ARRAY(SELECT delivery_date FROM removed WHERE action = 'clear') AS removed
"""


def _horizon_inputs(client, store: str) -> dict:
    """Today (America/Chicago), closing state, scheduled and operator-removed dates."""
    from core.datastore import _param_config, fq

    sql = INPUTS_SQL.format(
        edit_log=fq("inventory_edit_log"),
        closing=fq("inventory_closing_daily"),
        schedule=fq("inventory_restock_schedule"),
    )
    rows = list(client.query(sql, job_config=_param_config([("store", "STRING", store)])).result())
    r = rows[0]
    return {
        "today": _to_date(r["today"]),
        "today_closed": bool(r["today_closed"]),
        "scheduled": [_to_date(d) for d in (r["scheduled"] or [])],
        "removed": [_to_date(d) for d in (r["removed"] or [])],
    }


def _insert_schedule_if_absent(client, store: str, delivery_date: dt.date, by: str) -> bool:
    """INSERT one schedule row when missing. Returns True if a row was inserted."""
    from core.datastore import _param_config, fq

    job = client.query(
        f"""
        INSERT INTO {fq("inventory_restock_schedule")}
          (store, delivery_date, updated_at, updated_by)
        SELECT @store, @date, CURRENT_TIMESTAMP(), @by
        FROM (SELECT 1)
        WHERE NOT EXISTS (
          SELECT 1 FROM {fq("inventory_restock_schedule")}
          WHERE store = @store AND delivery_date = @date
        )
        """,
        job_config=_param_config([
            ("store", "STRING", store),
            ("date", "DATE", delivery_date.isoformat()),
            ("by", "STRING", by),
        ]),
    )
    job.result()
    n = getattr(job, "num_dml_affected_rows", None)
    return bool(n and n > 0)


def _log_schedule_set(client, store: str, delivery_date: dt.date, by: str) -> None:
    """Append to inventory_edit_log like console/Slack schedule sets. Best effort."""
    from core.datastore import _param_config, fq

    try:
        client.query(
            f"INSERT INTO {fq('inventory_edit_log')}"
            " (event_id, store, entity, action, delivery_date, item, key, new_value,"
            "  edited_by, edited_at, source)"
            " VALUES (GENERATE_UUID(), @store, 'restock_schedule', 'set', @date, NULL, NULL,"
            "  SAFE.PARSE_JSON(@value), @by, CURRENT_TIMESTAMP(), 'restock-horizon')",
            job_config=_param_config([
                ("store", "STRING", store),
                ("date", "DATE", delivery_date.isoformat()),
                ("value", "STRING", json.dumps(True)),
                ("by", "STRING", by),
            ]),
        ).result()
    except Exception as exc:  # noqa: BLE001 — the schedule row already landed
        logger.error(
            "inventory_edit_log_failed store=%s entity=restock_schedule action=set date=%s: %s",
            store, delivery_date.isoformat(), exc,
        )


def ensure_restock_horizon(
    store: str = "palmetto",
    *,
    by: str = AUTOMATION_BY,
) -> list[str]:
    """Top up live schedule dates from store_config. Returns ISO dates newly inserted."""
    from core.datastore import get_client
    from core.store_config import get_config

    weekday = get_config(store, WEEKDAY_KEY)
    if not weekday or not str(weekday).strip():
        return []
    client = get_client()
    if client is None:
        return []
    horizon = _parse_horizon(get_config(store, HORIZON_KEY))
    inputs = _horizon_inputs(client, store)
    planned = plan_horizon_additions(weekday=str(weekday), horizon=horizon, **inputs)
    if not planned:
        return []

    added: list[str] = []
    for d in planned:
        try:
            if _insert_schedule_if_absent(client, store, d, by):
                _log_schedule_set(client, store, d, by)
                added.append(d.isoformat())
        except Exception as exc:  # noqa: BLE001 — leave breadcrumb; don't abort reco
            logger.error(
                "restock_horizon_insert_failed store=%s date=%s by=%s exc=%s",
                store, d.isoformat(), by, exc,
            )
    if added:
        logger.info(
            "restock_horizon_added store=%s added=%s scheduled=%s today_closed=%s",
            store, ",".join(added),
            ",".join(x.isoformat() for x in sorted(inputs["scheduled"])),
            inputs["today_closed"],
        )
    return added
