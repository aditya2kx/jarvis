import "server-only";
import { dateParam, fq, q } from "@/lib/bq/client";
import type { PushRow } from "@/lib/labor/schedule-push";

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
