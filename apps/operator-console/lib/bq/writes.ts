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

export type RestockAction = "add-order" | "register-only" | "reset-to-estimated";

/**
 * One restock submission — mirrors handler.py::_handle_restock_submission's
 * three actions. Always registers the schedule first (even before any row
 * write, same as the Slack path), then always refreshes the reco at the end
 * regardless of which action ran.
 */
export async function submitRestock(
  store: string,
  deliveryDate: string,
  action: RestockAction,
  rows: { item: string; quantityTubs: number }[],
  by: string,
): Promise<void> {
  await setRestockSchedule(store, deliveryDate, by);
  if (action === "reset-to-estimated") {
    await clearRestockOrders(store, deliveryDate);
  } else if (action === "add-order") {
    await replaceRestockOrders(store, deliveryDate, rows, by);
  }
  // "register-only" writes nothing further — the date is now tracked.
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

/** Goal keys editable from the Home health scorecard's Goals drawer. */
export const GOAL_KEYS = [
  "goal_net_sales_weekly",
  "goal_net_sales_monthly",
  "goal_labor_pct_max",
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
     WHEN MATCHED THEN UPDATE SET note = @note, updated_at = CURRENT_TIMESTAMP(), updated_by = @by
     WHEN NOT MATCHED THEN INSERT (store, employee_name, date, note, updated_at, updated_by)
       VALUES (@store, @name, @date, @note, CURRENT_TIMESTAMP(), @by)`,
    { store, name: employeeName, date: dateParam(date), note, by },
  );
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
