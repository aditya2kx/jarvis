import "server-only";
import { randomUUID } from "node:crypto";
import { fq, mutate, q } from "@/lib/bq/client";
import { assignRowKey, type AssignItem, type AssignRow } from "@/lib/labor/open-shift-assign";

/** Insert one 'queued' row per open shift under a fresh assign_id; the ADP job picks them up. */
export async function queueAssign(store: string, items: AssignItem[], by: string): Promise<string> {
  const assignId = randomUUID();
  const rows = items.map((i) => ({
    row_key: assignRowKey(store, i),
    date: i.date,
    employee: i.employee,
    start_min: i.startMin,
    end_min: i.endMin,
  }));
  await mutate(
    `INSERT INTO ${fq("labor_open_shift_assignments")}
       (store, assign_id, row_key, date, employee, start_min, end_min,
        status, error, adp_name, requested_by, requested_at, updated_at)
     SELECT @store, @id, JSON_VALUE(s, '$.row_key'), DATE(JSON_VALUE(s, '$.date')),
            JSON_VALUE(s, '$.employee'), CAST(JSON_VALUE(s, '$.start_min') AS INT64),
            CAST(JSON_VALUE(s, '$.end_min') AS INT64), 'queued', NULL, NULL, @by,
            CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
     FROM UNNEST(JSON_QUERY_ARRAY(@items)) AS s`,
    { store, id: assignId, by, items: JSON.stringify(rows) },
  );
  return assignId;
}

/** Rows of one Write to ADP click (poll target while the ADP job runs). */
export async function assignRows(assignId: string): Promise<AssignRow[]> {
  return q<AssignRow>(
    `SELECT assign_id, row_key, CAST(date AS STRING) AS date, employee, start_min, end_min,
            status, error, adp_name, CAST(updated_at AS STRING) AS updated_at
     FROM ${fq("labor_open_shift_assignments")}
     WHERE assign_id = @id
     ORDER BY date, start_min`,
    { id: assignId },
  );
}
