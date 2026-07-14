import "server-only";
import { dateParam, fq, intParam, mutate, q } from "./client";

// Every write here mirrors the exact statement cloud/webhook/handler.py uses
// (see handler.py::_restock_set_schedule/_restock_clear_orders/
// _restock_replace_orders/_refresh_order_reco/_handle_config_set) so the app
// write path and the /bhaga-cloud Slack path converge on identical rows —
// never invent a different statement shape for the "same" write.

const DEFAULT_MAX_TUBS = 120;

/** MERGE the delivery date into inventory_restock_schedule (idempotent). */
export async function setRestockSchedule(store: string, deliveryDate: string, by: string): Promise<void> {
  await mutate(
    `MERGE ${fq("inventory_restock_schedule")} T
     USING (SELECT @store AS store, @date AS delivery_date) S
     ON T.store = S.store AND T.delivery_date = S.delivery_date
     WHEN MATCHED THEN UPDATE SET updated_at = CURRENT_TIMESTAMP(), updated_by = @by
     WHEN NOT MATCHED THEN INSERT (store, delivery_date, updated_at, updated_by)
       VALUES (@store, @date, CURRENT_TIMESTAMP(), @by)`,
    { store, date: dateParam(deliveryDate), by },
  );
}

/** DELETE all actual-order rows for (store, date) — "reset to estimated". */
export async function clearRestockOrders(store: string, deliveryDate: string): Promise<void> {
  await mutate(`DELETE FROM ${fq("inventory_restock_orders")} WHERE store = @store AND delivery_date = @date`, {
    store,
    date: dateParam(deliveryDate),
  });
}

/**
 * DELETE a registered delivery date from the schedule, then clear any actuals
 * for that date so nothing is orphaned. Console-only "Replace estimated date"
 * uses this; Slack has no schedule-DELETE path yet.
 */
export async function clearRestockSchedule(store: string, deliveryDate: string): Promise<void> {
  await mutate(`DELETE FROM ${fq("inventory_restock_schedule")} WHERE store = @store AND delivery_date = @date`, {
    store,
    date: dateParam(deliveryDate),
  });
  await clearRestockOrders(store, deliveryDate);
}

/**
 * Replace-per-date write: DELETE then INSERT, so re-uploading a corrected
 * CSV/parse for the same date always converges rather than accumulating
 * duplicates (mirrors handler.py::_restock_replace_orders — not atomic,
 * matching that same accepted tradeoff: a mid-write failure leaves the date
 * with zero actuals, never stale-but-present ones, and re-submit recovers).
 */
export async function replaceRestockOrders(
  store: string,
  deliveryDate: string,
  rows: { item: string; quantityTubs: number }[],
  by: string,
): Promise<void> {
  await clearRestockOrders(store, deliveryDate);
  if (!rows.length) return;

  const params: Record<string, unknown> = { store, date: dateParam(deliveryDate), by };
  const valuesSql = rows
    .map((_, i) => {
      params[`item${i}`] = rows[i].item;
      params[`qty${i}`] = rows[i].quantityTubs;
      return `(@store, @date, @item${i}, @qty${i}, @by, CURRENT_TIMESTAMP())`;
    })
    .join(", ");

  await mutate(
    `INSERT INTO ${fq("inventory_restock_orders")} (store, delivery_date, item, quantity_tubs, updated_by, updated_at)
     VALUES ${valuesSql}`,
    params,
  );
}

/**
 * Recompute inventory_order_reco for `store` — mirrors
 * core/order_reco.py::refresh_order_reco / handler.py::_refresh_order_reco.
 * Order matters: slot 2's TVF reads slot 1's materialized row, so slot 1's
 * INSERT must land before slot 2 runs. Call after any restock write or an
 * order_reco_max_tubs config change.
 */
export async function refreshOrderReco(store: string): Promise<void> {
  const cfgRows = await q<{ value: string }>(
    `SELECT value FROM ${fq("store_config")}
     WHERE store = @store AND key = 'order_reco_max_tubs'
     ORDER BY updated_at DESC LIMIT 1`,
    { store },
  );
  const maxTubs = intParam(cfgRows.length ? Number(cfgRows[0].value) : DEFAULT_MAX_TUBS);

  await mutate(`DELETE FROM ${fq("inventory_order_reco")} WHERE store = @store`, { store });
  await mutate(
    `INSERT INTO ${fq("inventory_order_reco")}
     SELECT @store, 1, t.*, CURRENT_TIMESTAMP() FROM ${fq("tvf_order_reco_slot1")}(@maxTubs) t`,
    { store, maxTubs },
  );
  // Slot 2 must run AFTER slot 1's INSERT lands — its TVF reads slot 1's row
  // back from inventory_order_reco (migration 031).
  await mutate(
    `INSERT INTO ${fq("inventory_order_reco")}
     SELECT @store, 2, t.*, CURRENT_TIMESTAMP() FROM ${fq("tvf_order_reco_slot2")}(@maxTubs) t`,
    { store, maxTubs },
  );
}

export type RestockAction = "add-order" | "register-only" | "reset-to-estimated" | "replace-estimated";

/**
 * One restock submission — mirrors handler.py::_handle_restock_submission's
 * three shared actions (add-order / register-only / reset-to-estimated).
 * Always registers the schedule first (even before any row write, same as
 * the Slack path), then always refreshes the reco at the end.
 * "replace-estimated" is console-only — use replaceEstimatedRestockDate.
 */
export async function submitRestock(
  store: string,
  deliveryDate: string,
  action: RestockAction,
  rows: { item: string; quantityTubs: number }[],
  by: string,
): Promise<void> {
  if (action === "replace-estimated") {
    throw new Error("submitRestock: use replaceEstimatedRestockDate for replace-estimated");
  }
  await setRestockSchedule(store, deliveryDate, by);
  if (action === "reset-to-estimated") {
    await clearRestockOrders(store, deliveryDate);
  } else if (action === "add-order") {
    await replaceRestockOrders(store, deliveryDate, rows, by);
  }
  // "register-only" writes nothing further — the date is now tracked.
  await refreshOrderReco(store);
}

/**
 * Console-only: move an Estimated schedule date (no actuals) from `fromDate`
 * to `toDate`, then recompute dual-date order reco so Order tubs / On hand
 * reflect the new lead days.
 */
export async function replaceEstimatedRestockDate(
  store: string,
  fromDate: string,
  toDate: string,
  by: string,
): Promise<void> {
  if (fromDate === toDate) {
    throw new Error("replaceEstimatedRestockDate: from and to dates must differ");
  }

  const scheduled = await q<{ n: number }>(
    `SELECT COUNT(*) AS n FROM ${fq("inventory_restock_schedule")}
     WHERE store = @store AND delivery_date = @date`,
    { store, date: dateParam(fromDate) },
  );
  if (!scheduled.length || Number(scheduled[0].n) === 0) {
    throw new Error(`replaceEstimatedRestockDate: ${fromDate} is not on the restock schedule`);
  }

  const actuals = await q<{ n: number }>(
    `SELECT COUNT(*) AS n FROM ${fq("inventory_restock_orders")}
     WHERE store = @store AND delivery_date = @date`,
    { store, date: dateParam(fromDate) },
  );
  if (actuals.length && Number(actuals[0].n) > 0) {
    throw new Error(
      `replaceEstimatedRestockDate: ${fromDate} has Actuals — only Estimated dates can be replaced`,
    );
  }

  await clearRestockSchedule(store, fromDate);
  await setRestockSchedule(store, toDate, by);
  await refreshOrderReco(store);
}

/** MERGE a store_config key (goals, capacity) — shared by M3 capacity edits and M4 goals. */
export async function setConfig(store: string, key: string, value: string, by: string): Promise<void> {
  await mutate(
    `MERGE ${fq("store_config")} T
     USING (SELECT @store AS store, @key AS key) S
     ON T.store = S.store AND T.key = S.key
     WHEN MATCHED THEN UPDATE SET value = @value, updated_at = CURRENT_TIMESTAMP(), updated_by = @by
     WHEN NOT MATCHED THEN INSERT (store, key, value, updated_at, updated_by)
       VALUES (@store, @key, @value, CURRENT_TIMESTAMP(), @by)`,
    { store, key, value, by },
  );
  if (key === "order_reco_max_tubs") {
    await refreshOrderReco(store);
  }
}

/** Goal keys editable from the Home Goal and Tracking scorecard / Goals drawer.
 *  Legacy food-cost / on-time / runway keys stay writable for Slack `config set`
 *  compatibility but are no longer shown on Home (Issue #158). */
export const GOAL_KEYS = [
  "goal_net_sales_weekly",
  "goal_net_sales_monthly",
  "goal_cash_flow_weekly",
  "goal_cash_flow_monthly",
  "goal_orders_per_day",
  "goal_labor_cost_weekly",
  "goal_labor_cost_monthly",
  "goal_ops_cost_weekly",
  "goal_ops_cost_monthly",
  "goal_total_cost_weekly",
  "goal_total_cost_monthly",
  "goal_hourly_labor_pct_max",
  "goal_labor_pct_max",
  "goal_kds_p95_min",
  "goal_bases_at_risk_max",
  "goal_food_cost_pct_max",
  "goal_speed_on_time_pct_min",
  "goal_inventory_runway_days_min",
] as const;
export type GoalKey = (typeof GOAL_KEYS)[number];

/** MERGE a single goal key — thin, named wrapper over setConfig (M4). */
export async function upsertGoal(store: string, key: GoalKey, value: string, by: string): Promise<void> {
  await setConfig(store, key, value, by);
}

/**
 * MERGE a per-shift training mark — mirrors handler.py::_handle_training_set's
 * exact statement (key store, employee_name, date) so the console and the
 * Slack `training set` command converge on the same rows. `name` must
 * already be the canonical employee name (the console has no alias
 * resolution — pick from the known-employee list, don't free-type).
 * Whole-day: clears exempt_start/exempt_end (Issue #167).
 */
export async function addTrainingShift(
  store: string,
  employeeName: string,
  date: string,
  by: string,
  note = "",
): Promise<void> {
  await mutate(
    `MERGE ${fq("training_shifts")} T
     USING (SELECT @store AS store, @name AS employee_name, @date AS date) S
     ON T.store = S.store AND T.employee_name = S.employee_name AND T.date = S.date
     WHEN MATCHED THEN UPDATE SET note = @note, exempt_start = NULL, exempt_end = NULL,
       updated_at = CURRENT_TIMESTAMP(), updated_by = @by
     WHEN NOT MATCHED THEN INSERT
       (store, employee_name, date, note, exempt_start, exempt_end, updated_at, updated_by)
       VALUES (@store, @name, @date, @note, NULL, NULL, CURRENT_TIMESTAMP(), @by)`,
    { store, name: employeeName, date: dateParam(date), note, by },
  );
}

export type TipExemptionDraft = {
  employeeName: string;
  date: string;
  mode: "clear" | "whole" | "window";
  exemptStart?: string;
  exemptEnd?: string;
  note?: string;
};

function assertHhmm(label: string, raw: string): string {
  const m = /^([01]?\d|2[0-3]):([0-5]\d)$/.exec(raw.trim());
  if (!m) throw new Error(`${label} must be HH:MM (got ${JSON.stringify(raw)})`);
  const h = m[1].padStart(2, "0");
  return `${h}:${m[2]}`;
}

/**
 * Batch tip-exemption writes for the unpaid current pay period only (Issue #170).
 * Rejects any draft date outside the unpaid ADP window.
 */
export async function applyTipExemptions(
  store: string,
  drafts: TipExemptionDraft[],
  by: string,
): Promise<void> {
  const { openPayPeriodBounds } = await import("@/lib/bq/queries");
  const open = await openPayPeriodBounds();
  if (!open) {
    throw new Error("No unpaid pay period found — tip exemptions cannot be edited.");
  }
  for (const d of drafts) {
    if (d.date < open.start || d.date > open.end) {
      throw new Error(
        `Tip exemptions are editable only for the unpaid current pay period ` +
          `(${open.start}..${open.end}); refused ${d.employeeName} on ${d.date}`,
      );
    }
  }

  for (const d of drafts) {
    if (d.mode === "clear") {
      await mutate(
        `DELETE FROM ${fq("training_shifts")}
         WHERE store=@store AND employee_name=@name AND date=@date`,
        { store, name: d.employeeName, date: dateParam(d.date) },
      );
      continue;
    }
    let start: string | null = null;
    let end: string | null = null;
    if (d.mode === "window") {
      if (!d.exemptStart || !d.exemptEnd) {
        throw new Error(`Window exemption for ${d.employeeName} on ${d.date} needs start and end`);
      }
      start = assertHhmm("exemptStart", d.exemptStart);
      end = assertHhmm("exemptEnd", d.exemptEnd);
      const [sh, sm] = start.split(":").map(Number);
      const [eh, em] = end.split(":").map(Number);
      if (eh * 60 + em <= sh * 60 + sm) {
        throw new Error(`exempt end must be after start for ${d.employeeName} on ${d.date}`);
      }
    }
    const note = d.note ?? "";
    await mutate(
      `MERGE ${fq("training_shifts")} T
       USING (SELECT @store AS store, @name AS employee_name, @date AS date) S
       ON T.store = S.store AND T.employee_name = S.employee_name AND T.date = S.date
       WHEN MATCHED THEN UPDATE SET
         note = @note, exempt_start = @start, exempt_end = @end,
         updated_at = CURRENT_TIMESTAMP(), updated_by = @by
       WHEN NOT MATCHED THEN INSERT
         (store, employee_name, date, note, exempt_start, exempt_end, updated_at, updated_by)
         VALUES (@store, @name, @date, @note, @start, @end, CURRENT_TIMESTAMP(), @by)`,
      {
        store,
        name: d.employeeName,
        date: dateParam(d.date),
        note,
        start,
        end,
        by,
      },
    );
  }
}

/**
 * MERGE a manual recognition bonus (migration 033) — key
 * (store, pay_period, employee). amount_cents is integer cents.
 */
export async function addRecognitionBonus(
  store: string,
  payPeriod: string,
  employee: string,
  amountCents: number,
  reason: string,
  by: string,
): Promise<void> {
  await mutate(
    `MERGE ${fq("recognition_bonuses")} T
     USING (SELECT @store AS store, @period AS pay_period, @employee AS employee) S
     ON T.store = S.store AND T.pay_period = S.pay_period AND T.employee = S.employee
     WHEN MATCHED THEN UPDATE SET amount_cents = @cents, reason = @reason,
       updated_at = CURRENT_TIMESTAMP(), updated_by = @by
     WHEN NOT MATCHED THEN INSERT (store, pay_period, employee, amount_cents, reason, updated_at, updated_by)
       VALUES (@store, @period, @employee, @cents, @reason, CURRENT_TIMESTAMP(), @by)`,
    { store, period: payPeriod, employee, cents: intParam(amountCents), reason, by },
  );
}

/** Upsert a linked Plaid Item metadata row (access_token stays in Secret Manager). */
export async function upsertPlaidItem(
  store: string,
  itemId: string,
  institutionName: string | null,
  by: string,
): Promise<void> {
  await mutate(
    `MERGE ${fq("plaid_items")} T
     USING (SELECT @store AS store, @item_id AS item_id) S
     ON T.store = S.store AND T.item_id = S.item_id
     WHEN MATCHED THEN UPDATE SET
       institution_name = @institution_name,
       linked_by = @by,
       linked_at = CURRENT_TIMESTAMP()
     WHEN NOT MATCHED THEN INSERT
       (store, item_id, institution_name, cursor, linked_at, linked_by, last_synced_at)
       VALUES (@store, @item_id, @institution_name, '', CURRENT_TIMESTAMP(), @by, NULL)`,
    { store, item_id: itemId, institution_name: institutionName, by },
  );
}

export async function updatePlaidCursor(store: string, itemId: string, cursor: string): Promise<void> {
  await mutate(
    `UPDATE ${fq("plaid_items")}
     SET cursor = @cursor, last_synced_at = CURRENT_TIMESTAMP()
     WHERE store = @store AND item_id = @item_id`,
    { store, item_id: itemId, cursor },
  );
}

export interface PlaidTxnWrite {
  transaction_id: string;
  item_id: string;
  account_id: string | null;
  date: string | null;
  name: string | null;
  merchant_name: string | null;
  amount: number | null;
  iso_currency: string | null;
  pending: boolean;
  pfc_primary: string | null;
  pfc_detailed: string | null;
  raw_json: string;
}

/** Idempotent per-row MERGE for one Plaid transaction. */
export async function upsertPlaidTransaction(row: PlaidTxnWrite): Promise<void> {
  await mutate(
    `MERGE ${fq("plaid_transactions")} T
     USING (SELECT @transaction_id AS transaction_id) S
     ON T.transaction_id = S.transaction_id
     WHEN MATCHED THEN UPDATE SET
       item_id = @item_id, account_id = @account_id,
       date = SAFE.PARSE_DATE('%Y-%m-%d', @date),
       name = @name, merchant_name = @merchant_name, amount = @amount,
       iso_currency = @iso_currency, pending = @pending,
       pfc_primary = @pfc_primary, pfc_detailed = @pfc_detailed,
       raw_json = @raw_json, updated_at = CURRENT_TIMESTAMP()
     WHEN NOT MATCHED THEN INSERT (
       transaction_id, item_id, account_id, date, name, merchant_name,
       amount, iso_currency, pending, pfc_primary, pfc_detailed, raw_json, updated_at
     ) VALUES (
       @transaction_id, @item_id, @account_id, SAFE.PARSE_DATE('%Y-%m-%d', @date),
       @name, @merchant_name, @amount, @iso_currency, @pending,
       @pfc_primary, @pfc_detailed, @raw_json, CURRENT_TIMESTAMP()
     )`,
    {
      transaction_id: row.transaction_id,
      item_id: row.item_id,
      account_id: row.account_id,
      date: row.date,
      name: row.name,
      merchant_name: row.merchant_name,
      amount: row.amount,
      iso_currency: row.iso_currency,
      pending: row.pending,
      pfc_primary: row.pfc_primary,
      pfc_detailed: row.pfc_detailed,
      raw_json: row.raw_json,
    },
  );
}

export async function deletePlaidTransactions(ids: string[]): Promise<void> {
  if (!ids.length) return;
  await mutate(`DELETE FROM ${fq("plaid_transactions")} WHERE transaction_id IN UNNEST(@ids)`, {
    ids,
  });
}
