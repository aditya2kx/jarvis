import "server-only";
import { randomUUID } from "node:crypto";
import { fq, mutate, q } from "@/lib/bq/client";
import { shiftText, type AdpChange, type ApplyRow, type ShiftChange } from "@/lib/labor/adp-changes";
import type { StagedChange } from "@/lib/labor/availability-changes";

function shiftRow(change_id: string, c: ShiftChange) {
  return {
    change_id,
    kind: c.kind,
    date: c.date,
    employee: "employee" in c ? c.employee : null,
    start_min: c.startMin,
    end_min: c.endMin,
    to_start_min: "to" in c ? c.to.startMin : null,
    to_end_min: "to" in c ? c.to.endMin : null,
    to_employee: c.kind === "shift-reassign" ? c.toEmployee : null,
    count: c.kind === "open-add" ? c.count : null,
    summary: shiftText(c),
  };
}

function unavailabilityRow(change_id: string, c: StagedChange) {
  const fields = c.kind === "add" || c.kind === "edit" ? c : null;
  return {
    change_id,
    kind: c.kind,
    row_key: "rowKey" in c ? c.rowKey : null,
    employee: c.employee,
    scope: "scope" in c ? c.scope : null,
    first_date: fields ? fields.date : c.kind === "delete" ? c.date : null,
    all_day: fields ? fields.allDay : null,
    from_time: fields && !fields.allDay ? fields.from : null,
    to_time: fields && !fields.allDay ? fields.to : null,
    weekly: fields ? fields.weekly : null,
    repeat_until: fields?.weekly ? fields.until : null,
    summary: c.summary,
  };
}

/**
 * One Apply: a 'queued' row per change under one batch_id (shifts → adp_shift_changes,
 * unavailability → adp_unavailability_changes). Returns the change ids in input order.
 */
export async function queueAdpChanges(
  store: string,
  changes: AdpChange[],
  by: string,
): Promise<{ batchId: string; ids: string[] }> {
  const batchId = randomUUID();
  const ids = changes.map(() => randomUUID());
  const shifts = changes.flatMap((a, i) => (a.area === "shift" ? [shiftRow(ids[i]!, a.c)] : []));
  const unavail = changes.flatMap((a, i) => (a.area === "unavailability" ? [unavailabilityRow(ids[i]!, a.c)] : []));
  if (shifts.length) {
    await mutate(
      `INSERT INTO ${fq("adp_shift_changes")}
         (store, batch_id, change_id, kind, date, employee, start_min, end_min, to_start_min, to_end_min,
          to_employee, count, summary, status, error, adp_name, requested_by, requested_at, updated_at)
       SELECT @store, @batch, JSON_VALUE(s, '$.change_id'), JSON_VALUE(s, '$.kind'), DATE(JSON_VALUE(s, '$.date')),
              JSON_VALUE(s, '$.employee'), CAST(JSON_VALUE(s, '$.start_min') AS INT64),
              CAST(JSON_VALUE(s, '$.end_min') AS INT64), CAST(JSON_VALUE(s, '$.to_start_min') AS INT64),
              CAST(JSON_VALUE(s, '$.to_end_min') AS INT64), JSON_VALUE(s, '$.to_employee'),
              CAST(JSON_VALUE(s, '$.count') AS INT64), JSON_VALUE(s, '$.summary'), 'queued', NULL, NULL, @by,
              CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
       FROM UNNEST(JSON_QUERY_ARRAY(@items)) AS s`,
      { store, batch: batchId, by, items: JSON.stringify(shifts) },
    );
  }
  if (unavail.length) {
    await mutate(
      `INSERT INTO ${fq("adp_unavailability_changes")}
         (store, batch_id, change_id, kind, row_key, employee, scope, first_date, all_day, from_time, to_time,
          weekly, repeat_until, summary, status, error, requested_by, requested_at, updated_at)
       SELECT @store, @batch, JSON_VALUE(s, '$.change_id'), JSON_VALUE(s, '$.kind'), JSON_VALUE(s, '$.row_key'),
              JSON_VALUE(s, '$.employee'), JSON_VALUE(s, '$.scope'), DATE(JSON_VALUE(s, '$.first_date')),
              CAST(JSON_VALUE(s, '$.all_day') AS BOOL), JSON_VALUE(s, '$.from_time'), JSON_VALUE(s, '$.to_time'),
              CAST(JSON_VALUE(s, '$.weekly') AS BOOL), DATE(JSON_VALUE(s, '$.repeat_until')),
              JSON_VALUE(s, '$.summary'), 'queued', NULL, @by, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
       FROM UNNEST(JSON_QUERY_ARRAY(@items)) AS s`,
      { store, batch: batchId, by, items: JSON.stringify(unavail) },
    );
  }
  return { batchId, ids };
}

/** Every row of one Apply, shifts first (poll target while the ADP run works through them). */
export async function applyRows(batchId: string): Promise<ApplyRow[]> {
  return q<ApplyRow>(
    `SELECT change_id, 'shift' AS area, summary, status, error, date AS d
     FROM ${fq("adp_shift_changes")} WHERE batch_id = @id
     UNION ALL
     SELECT change_id, 'unavailability' AS area, summary, status, error, first_date AS d
     FROM ${fq("adp_unavailability_changes")} WHERE batch_id = @id
     ORDER BY area, d`,
    { id: batchId },
  );
}
