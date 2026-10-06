import "server-only";
import { randomUUID } from "node:crypto";
import { dateParam, fq, mutate, q } from "@/lib/bq/client";
import { pushRowKey, type PushRow, type PushShift } from "@/lib/labor/schedule-push";

/** Insert one 'queued' row per shift under a fresh push_id; the ADP job picks them up. */
export async function queueDraftPush(
  store: string,
  weekStart: string,
  shifts: PushShift[],
  by: string,
): Promise<string> {
  const pushId = randomUUID();
  const rows = shifts.map((s) => ({
    row_key: pushRowKey(store, s),
    date: s.date,
    employee: s.employee,
    start_min: s.startMin,
    end_min: s.endMin,
  }));
  await mutate(
    `INSERT INTO ${fq("labor_schedule_pushes")}
       (store, push_id, week_start, row_key, date, employee, start_min, end_min,
        status, error, requested_by, requested_at, updated_at)
     SELECT @store, @push, @week, JSON_VALUE(s, '$.row_key'), DATE(JSON_VALUE(s, '$.date')),
            JSON_VALUE(s, '$.employee'), CAST(JSON_VALUE(s, '$.start_min') AS INT64),
            CAST(JSON_VALUE(s, '$.end_min') AS INT64), 'queued', NULL, @by,
            CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
     FROM UNNEST(JSON_QUERY_ARRAY(@shifts)) AS s`,
    { store, push: pushId, week: dateParam(weekStart), by, shifts: JSON.stringify(rows) },
  );
  return pushId;
}

/**
 * Latest row per shift for the week (a re-save supersedes an earlier failed row).
 * A 'skipped' duplicate never hides the row that actually drafted the shift.
 */
export async function weekPushRows(store: string, weekStart: string): Promise<PushRow[]> {
  return q<PushRow>(
    `SELECT push_id, row_key, CAST(date AS STRING) AS date, employee, start_min, end_min,
            status, error, CAST(updated_at AS STRING) AS updated_at
     FROM ${fq("labor_schedule_pushes")}
     WHERE store = @store AND week_start = @week
     QUALIFY ROW_NUMBER() OVER (
       PARTITION BY row_key ORDER BY status = 'skipped', requested_at DESC, updated_at DESC
     ) = 1
     ORDER BY date, start_min`,
    { store, week: dateParam(weekStart) },
  );
}

/** Latest row per shift for every week starting on/after ``fromWeek`` — the saved plans. */
export async function upcomingPushRows(store: string, fromWeek: string): Promise<(PushRow & { week_start: string })[]> {
  return q<PushRow & { week_start: string }>(
    `SELECT push_id, row_key, CAST(week_start AS STRING) AS week_start, CAST(date AS STRING) AS date,
            employee, start_min, end_min, status, error, CAST(updated_at AS STRING) AS updated_at
     FROM ${fq("labor_schedule_pushes")}
     WHERE store = @store AND week_start >= @week
     QUALIFY ROW_NUMBER() OVER (
       PARTITION BY row_key ORDER BY status = 'skipped', requested_at DESC, updated_at DESC
     ) = 1
     ORDER BY date, start_min`,
    { store, week: dateParam(fromWeek) },
  );
}

/** Rows of one push (poll target while the ADP job runs). */
export async function pushRows(pushId: string): Promise<PushRow[]> {
  return q<PushRow>(
    `SELECT push_id, row_key, CAST(date AS STRING) AS date, employee, start_min, end_min,
            status, error, CAST(updated_at AS STRING) AS updated_at
     FROM ${fq("labor_schedule_pushes")}
     WHERE push_id = @push
     ORDER BY date, start_min`,
    { push: pushId },
  );
}
