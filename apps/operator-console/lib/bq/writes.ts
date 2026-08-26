import "server-only";
import { dateParam, fq, intParam, mutate, q, timestampParam } from "./client";

// Every write here mirrors the exact statement cloud/webhook/handler.py uses
// (see handler.py::_restock_set_schedule/_restock_clear_orders/
// _restock_replace_orders/_refresh_order_reco/_handle_config_set) so the app
// write path and the /bhaga-cloud Slack path converge on identical rows —
// never invent a different statement shape for the "same" write.

const DEFAULT_MAX_TUBS = 120;

export type RecoRefreshOpts = {
  /** When true, caller enqueues durable order-reco refresh (Issue #175 Option B). */
  skipRefresh?: boolean;
};

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
  await clearOrderTubOverrides(store, deliveryDate);
}

/** DELETE manual Order Tubs pins for (store, date). */
export async function clearOrderTubOverrides(store: string, deliveryDate: string): Promise<void> {
  await mutate(
    `DELETE FROM ${fq("inventory_order_tub_overrides")} WHERE store = @store AND delivery_date = @date`,
    { store, date: dateParam(deliveryDate) },
  );
}

/**
 * Replace-per-date manual Order Tubs pins (Issue #225). Empty `rows` clears all
 * pins for the date (all bases back to Estimated water-fill). Does not touch
 * Actuals. Caller refreshes reco once after save.
 */
export async function replaceOrderTubOverrides(
  store: string,
  deliveryDate: string,
  rows: { item: string; quantityTubs: number }[],
  by: string,
  opts: RecoRefreshOpts = {},
): Promise<void> {
  for (const r of rows) {
    if (!Number.isInteger(r.quantityTubs) || r.quantityTubs < 0) {
      throw new Error(`Invalid tub count for ${r.item}: ${r.quantityTubs}`);
    }
    if (r.item === "Blade" || r.item === "TOTAL") {
      throw new Error(`Cannot pin Order Tubs for ${r.item}`);
    }
  }
  const sum = rows.reduce((acc, r) => acc + r.quantityTubs, 0);
  const cfgRows = await q<{ value: string }>(
    `SELECT value FROM ${fq("store_config")}
     WHERE store = @store AND key = 'order_reco_max_tubs'
     ORDER BY updated_at DESC LIMIT 1`,
    { store },
  );
  const maxTubs = cfgRows.length ? Number(cfgRows[0].value) : DEFAULT_MAX_TUBS;
  if (sum > maxTubs) {
    throw new Error(
      `Manual Order Tubs sum (${sum}) exceeds capacity (${maxTubs}). Lower pins or raise capacity.`,
    );
  }

  await clearOrderTubOverrides(store, deliveryDate);
  if (rows.length) {
    const params: Record<string, unknown> = { store, date: dateParam(deliveryDate), by };
    const valuesSql = rows
      .map((_, i) => {
        params[`item${i}`] = rows[i].item;
        params[`qty${i}`] = intParam(rows[i].quantityTubs);
        return `(@store, @date, @item${i}, @qty${i}, @by, CURRENT_TIMESTAMP())`;
      })
      .join(", ");
    await mutate(
      `INSERT INTO ${fq("inventory_order_tub_overrides")}
         (store, delivery_date, item, quantity_tubs, updated_by, updated_at)
       VALUES ${valuesSql}`,
      params,
    );
  }
  if (!opts.skipRefresh) await refreshOrderReco(store);
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
 * Order matters: slot N's TVF reads slot N-1's materialized row (latest
 * refreshed_at, migration 067), so earlier INSERTs must land first. After all
 * slots insert with a shared generation timestamp, DELETE prior generations.
 * Call after any restock write or an
 * order_reco_max_tubs config change. Slot count follows live
 * vw_order_reco_next_dates (migration 052, default cap 4).
 */
export async function refreshOrderReco(store: string): Promise<void> {
  const [cfgRows, slotRows] = await Promise.all([
    q<{ value: string }>(
      `SELECT value FROM ${fq("store_config")}
       WHERE store = @store AND key = 'order_reco_max_tubs'
       ORDER BY updated_at DESC LIMIT 1`,
      { store },
    ),
    q<{ slot: number }>(`SELECT slot FROM ${fq("vw_order_reco_next_dates")} ORDER BY slot`),
  ]);
  const maxTubs = intParam(cfgRows.length ? Number(cfgRows[0].value) : DEFAULT_MAX_TUBS);
  const slots = slotRows.map((r) => Number(r.slot)).filter((n) => Number.isFinite(n));
  const gen = timestampParam(new Date());

  // Explicit columns — migration 041 added delivery_date; t.* + ts would mis-map.
  const cols =
    "store, Slot, Item, `Current Qty`, `Avg per day`, `On Hand at Restock`, " +
    "`Order Tubs`, `Order Weight lbs`, `After Restock`, `Days Left After Restock`, " +
    "_ord, refreshed_at, delivery_date";
  const sel =
    "Item, `Current Qty`, `Avg per day`, `On Hand at Restock`, " +
    "`Order Tubs`, `Order Weight lbs`, `After Restock`, `Days Left After Restock`, " +
    "_ord, @gen, delivery_date";

  if (!slots.length) {
    await mutate(`DELETE FROM ${fq("inventory_order_reco")} WHERE store = @store`, { store });
    return;
  }

  await mutate(
    `INSERT INTO ${fq("inventory_order_reco")} (${cols})
     SELECT @store, 1, ${sel} FROM ${fq("tvf_order_reco_slot1")}(@maxTubs)`,
    { store, maxTubs, gen },
  );
  for (const slot of slots) {
    if (slot < 2) continue;
    await mutate(
      `INSERT INTO ${fq("inventory_order_reco")} (${cols})
       SELECT @store, @slot, ${sel} FROM ${fq("tvf_order_reco_slot_n")}(@maxTubs, @slot)`,
      { store, maxTubs, slot: intParam(slot), gen },
    );
  }
  await mutate(
    `DELETE FROM ${fq("inventory_order_reco")} WHERE store = @store AND refreshed_at != @gen`,
    { store, gen },
  );
}

export type EnsureOrderRecoResult =
  | { status: "fresh" }
  | { status: "refreshed" }
  | { status: "queued" };

/**
 * Self-heal when live next-delivery dates and materialized reco rows diverge
 * (e.g. Chicago midnight rolled Slot 1 to a new calendar date but nightly
 * refresh has not run yet). Also refreshes when refreshed_at's CT date is
 * before today. Idempotent — no-op when already aligned.
 *
 * When `enqueue` is provided and the reco is stale, calls enqueue instead of
 * blocking the RSC on inline TVFs (Issue #175 Option B).
 */
export async function ensureOrderRecoFresh(
  store: string,
  opts: { enqueue?: () => Promise<void> } = {},
): Promise<EnsureOrderRecoResult> {
  const [next, mat, todayRows, refreshedRows, dupRows] = await Promise.all([
    q<{ delivery_date: string }>(
      `SELECT CAST(delivery_date AS STRING) AS delivery_date
       FROM ${fq("vw_order_reco_next_dates")} ORDER BY slot`,
    ),
    q<{ delivery_date: string | null }>(
      `SELECT DISTINCT CAST(delivery_date AS STRING) AS delivery_date
       FROM ${fq("inventory_order_reco")}
       WHERE store = @store AND Item = 'TOTAL'`,
      { store },
    ),
    q<{ today: string }>(`SELECT CAST(CURRENT_DATE('America/Chicago') AS STRING) AS today`),
    q<{ refreshed_ct: string | null }>(
      `SELECT CAST(DATE(MAX(refreshed_at), 'America/Chicago') AS STRING) AS refreshed_ct
       FROM ${fq("inventory_order_reco")} WHERE store = @store`,
      { store },
    ),
    // Concurrent refresh races leave duplicate (store, Slot, Item) rows — date
    // sets still "match", so detect dups explicitly (Issue #238 localhost race).
    q<{ n: number }>(
      `SELECT COUNT(*) AS n FROM (
         SELECT Slot, Item FROM ${fq("inventory_order_reco")}
         WHERE store = @store
         GROUP BY Slot, Item
         HAVING COUNT(*) > 1
       )`,
      { store },
    ),
  ]);

  const live = new Set(next.map((d) => d.delivery_date.slice(0, 10)));
  const have = new Set(
    mat
      .map((r) => (r.delivery_date == null ? "" : r.delivery_date.slice(0, 10)))
      .filter(Boolean),
  );
  const today = todayRows[0]?.today ?? "";
  const refreshedCt = refreshedRows[0]?.refreshed_ct ?? "";
  const datesMatch = live.size === have.size && [...live].every((d) => have.has(d));
  const staleDay = Boolean(today && refreshedCt && refreshedCt < today);
  const hasDupes = Boolean(dupRows.length && Number(dupRows[0].n) > 0);

  if (!datesMatch || staleDay || hasDupes || (live.size === 0 && have.size > 0)) {
    if (opts.enqueue) {
      await opts.enqueue();
      return { status: "queued" };
    }
    await refreshOrderReco(store);
    return { status: "refreshed" };
  }
  return { status: "fresh" };
}

export type RestockAction =
  | "add-order"
  | "register-only"
  | "reset-to-estimated"
  | "replace-estimated"
  | "move-date"
  | "remove-date";

/**
 * One restock submission — mirrors handler.py::_handle_restock_submission's
 * three shared actions (add-order / register-only / reset-to-estimated).
 * Always registers the schedule first (even before any row write, same as
 * the Slack path), then always refreshes the reco at the end.
 * Console-only move/remove/replace use dedicated helpers — not submitRestock.
 */
export async function submitRestock(
  store: string,
  deliveryDate: string,
  action: RestockAction,
  rows: { item: string; quantityTubs: number }[],
  by: string,
  opts: RecoRefreshOpts = {},
): Promise<void> {
  if (action === "replace-estimated") {
    throw new Error("submitRestock: use replaceEstimatedRestockDate for replace-estimated");
  }
  if (action === "move-date") {
    throw new Error("submitRestock: use moveRestockDate for move-date");
  }
  if (action === "remove-date") {
    throw new Error("submitRestock: use removeRestockDate for remove-date");
  }
  await setRestockSchedule(store, deliveryDate, by);
  if (action === "reset-to-estimated") {
    await clearRestockOrders(store, deliveryDate);
  } else if (action === "add-order") {
    await replaceRestockOrders(store, deliveryDate, rows, by);
  }
  // "register-only" writes nothing further — the date is now tracked.
  if (!opts.skipRefresh) await refreshOrderReco(store);
}

/**
 * Console-only: rekey a scheduled delivery from `fromDate` → `toDate`, carrying
 * Actuals and Manual tub overrides. Fixes wrong-date Actuals (e.g. 8/17 → 8/20)
 * in one step. Reads rows before clear so nothing is orphaned.
 */
export async function moveRestockDate(
  store: string,
  fromDate: string,
  toDate: string,
  by: string,
  opts: RecoRefreshOpts = {},
): Promise<void> {
  if (fromDate === toDate) {
    throw new Error("moveRestockDate: from and to dates must differ");
  }

  const scheduled = await q<{ n: number }>(
    `SELECT COUNT(*) AS n FROM ${fq("inventory_restock_schedule")}
     WHERE store = @store AND delivery_date = @date`,
    { store, date: dateParam(fromDate) },
  );
  if (!scheduled.length || Number(scheduled[0].n) === 0) {
    throw new Error(`moveRestockDate: ${fromDate} is not on the restock schedule`);
  }

  const [orderRows, overrideRows] = await Promise.all([
    q<{ item: string; quantity_tubs: number }>(
      `SELECT item, quantity_tubs FROM ${fq("inventory_restock_orders")}
       WHERE store = @store AND delivery_date = @date`,
      { store, date: dateParam(fromDate) },
    ),
    q<{ item: string; quantity_tubs: number }>(
      `SELECT item, quantity_tubs FROM ${fq("inventory_order_tub_overrides")}
       WHERE store = @store AND delivery_date = @date`,
      { store, date: dateParam(fromDate) },
    ),
  ]);

  await clearRestockSchedule(store, fromDate);
  await setRestockSchedule(store, toDate, by);

  if (orderRows.length) {
    // Prefer Actuals over Manual pins when both exist (Actuals date makes overrides moot).
    await replaceRestockOrders(
      store,
      toDate,
      orderRows.map((r) => ({ item: r.item, quantityTubs: Number(r.quantity_tubs) })),
      by,
    );
  } else if (overrideRows.length) {
    await replaceOrderTubOverrides(
      store,
      toDate,
      overrideRows.map((r) => ({ item: r.item, quantityTubs: Number(r.quantity_tubs) })),
      by,
      { skipRefresh: true },
    );
  }

  if (!opts.skipRefresh) await refreshOrderReco(store);
}

/** Console-only: delete a registered delivery date (schedule + actuals + overrides). */
export async function removeRestockDate(
  store: string,
  deliveryDate: string,
  by: string,
  opts: RecoRefreshOpts = {},
): Promise<void> {
  void by; // audited via caller identity; DELETE rows don't store updated_by
  const scheduled = await q<{ n: number }>(
    `SELECT COUNT(*) AS n FROM ${fq("inventory_restock_schedule")}
     WHERE store = @store AND delivery_date = @date`,
    { store, date: dateParam(deliveryDate) },
  );
  if (!scheduled.length || Number(scheduled[0].n) === 0) {
    throw new Error(`removeRestockDate: ${deliveryDate} is not on the restock schedule`);
  }
  await clearRestockSchedule(store, deliveryDate);
  if (!opts.skipRefresh) await refreshOrderReco(store);
}

/**
 * Console-only: move an Estimated schedule date (no actuals) from `fromDate`
 * to `toDate`. Prefer moveRestockDate when Actuals may be present.
 */
export async function replaceEstimatedRestockDate(
  store: string,
  fromDate: string,
  toDate: string,
  by: string,
  opts: RecoRefreshOpts = {},
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
  if (!opts.skipRefresh) await refreshOrderReco(store);
}

/** MERGE a store_config key (goals, capacity) — shared by M3 capacity edits and M4 goals. */
export async function setConfig(
  store: string,
  key: string,
  value: string,
  by: string,
  opts: RecoRefreshOpts = {},
): Promise<void> {
  await mutate(
    `MERGE ${fq("store_config")} T
     USING (SELECT @store AS store, @key AS key) S
     ON T.store = S.store AND T.key = S.key
     WHEN MATCHED THEN UPDATE SET value = @value, updated_at = CURRENT_TIMESTAMP(), updated_by = @by
     WHEN NOT MATCHED THEN INSERT (store, key, value, updated_at, updated_by)
       VALUES (@store, @key, @value, CURRENT_TIMESTAMP(), @by)`,
    { store, key, value, by },
  );
  if (key === "order_reco_max_tubs" && !opts.skipRefresh) {
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
  "goal_labor_hours_week",
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
 * Batch tip-exemption writes for any unpaid ADP pay period (Issue #170).
 * Rejects drafts whose date falls outside every unpaid window (just-ended
 * unpaid biweek and/or in-progress calendar biweek).
 */
export async function applyTipExemptions(
  store: string,
  drafts: TipExemptionDraft[],
  by: string,
): Promise<void> {
  const { unpaidPayPeriodWindows } = await import("@/lib/bq/queries");
  const { dateInUnpaidWindows } = await import("@/lib/payroll/openPeriod");
  const windows = await unpaidPayPeriodWindows();
  if (!windows.length) {
    throw new Error("No unpaid pay period found — tip exemptions cannot be edited.");
  }
  const windowLabel = windows.map((w) => `${w.start}..${w.end}`).join(" | ");
  for (const d of drafts) {
    if (!dateInUnpaidWindows(d.date, windows)) {
      throw new Error(
        `Tip exemptions are editable only for unpaid pay periods ` +
          `(${windowLabel}); refused ${d.employeeName} on ${d.date}`,
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
    // whole-day mode binds start/end as null — Node BQ client needs explicit
    // STRING types for null params (window mode still passes the same types).
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
      { start: "STRING", end: "STRING" },
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

export async function addEmployeePerk(
  store: string,
  employee: string,
  perkId: string,
  amountCents: number,
  payPeriod: string,
  cadence: string,
  adpEarningDescription: string,
  by: string,
): Promise<void> {
  await mutate(
    `MERGE ${fq("employee_perks")} T
     USING (SELECT @store AS store, @employee AS employee, @perk_id AS perk_id,
                   @pay_period AS pay_period) S
     ON T.store = S.store AND T.employee = S.employee
       AND T.perk_id = S.perk_id AND IFNULL(T.pay_period, '') = S.pay_period
     WHEN MATCHED THEN UPDATE SET amount_cents = @cents, cadence = @cadence,
       adp_earning_description = @desc,
       updated_at = CURRENT_TIMESTAMP(), updated_by = @by
     WHEN NOT MATCHED THEN INSERT
       (store, employee, perk_id, amount_cents, cadence, adp_earning_description,
        pay_period, updated_at, updated_by)
       VALUES (@store, @employee, @perk_id, @cents, @cadence, @desc,
        @pay_period, CURRENT_TIMESTAMP(), @by)`,
    {
      store,
      employee,
      perk_id: perkId,
      cents: intParam(amountCents),
      cadence,
      desc: adpEarningDescription,
      pay_period: payPeriod,
      by,
    },
  );
}

/** Upsert a linked Plaid Item metadata row (access_token stays in Secret Manager). */
export async function upsertPlaidItem(
  store: string,
  itemId: string,
  institutionName: string | null,
  by: string,
): Promise<void> {
  // institution_name is often null at Link time — Node BQ client needs an
  // explicit STRING type for null params (same pattern as tip exemptions).
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
    { institution_name: "STRING" },
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
  // Several Plaid fields are routinely null — declare types so the Node BQ
  // client can bind them (avoids "Parameter types must be provided for null").
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
    {
      account_id: "STRING",
      date: "STRING",
      name: "STRING",
      merchant_name: "STRING",
      amount: "FLOAT64",
      iso_currency: "STRING",
      pfc_primary: "STRING",
      pfc_detailed: "STRING",
    },
  );
}

export async function deletePlaidTransactions(ids: string[]): Promise<void> {
  if (!ids.length) return;
  await mutate(`DELETE FROM ${fq("plaid_transactions")} WHERE transaction_id IN UNNEST(@ids)`, {
    ids,
  });
}

/**
 * Remove duplicate plaid_transactions rows (same transaction_id).
 * Concurrent MERGE inserts can race under dual sync paths (Issue #230).
 *
 * Uses CREATE OR REPLACE … WHERE rn = 1 (not DELETE on transaction_id+updated_at).
 * Same-timestamp duplicates from one MERGE would otherwise both match the DELETE
 * STRUCT and wipe the transaction (PR #236 Claude blocking).
 */
export async function dedupePlaidTransactions(): Promise<number> {
  const before = await q<{ n: number }>(
    `SELECT COUNT(*) - COUNT(DISTINCT transaction_id) AS n
     FROM ${fq("plaid_transactions")}`,
  );
  const extras = Number(before[0]?.n ?? 0);
  if (!(extras > 0)) return 0;
  await mutate(
    `CREATE OR REPLACE TABLE ${fq("plaid_transactions")} AS
     SELECT * EXCEPT (rn)
     FROM (
       SELECT
         *,
         ROW_NUMBER() OVER (
           PARTITION BY transaction_id
           ORDER BY updated_at DESC NULLS LAST, TO_JSON_STRING(raw_json)
         ) AS rn
       FROM ${fq("plaid_transactions")}
     )
     WHERE rn = 1`,
  );
  return extras;
}

/** Operator toggle: exclude (or include) a txn from Money out / category rollups. */
export async function setPlaidTransactionInternal(
  transactionId: string,
  isInternal: boolean,
): Promise<void> {
  await mutate(
    `UPDATE ${fq("plaid_transactions")}
     SET is_internal = @is_internal, updated_at = CURRENT_TIMESTAMP()
     WHERE transaction_id = @transaction_id`,
    { transaction_id: transactionId, is_internal: isInternal },
    { is_internal: "BOOL" },
  );
}

/** Batch-flag transfer legs: mirror is_internal + assign Internal transfers category. */
export async function markPlaidTransactionsInternal(ids: string[]): Promise<number> {
  if (!ids.length) return 0;
  await mutate(
    `UPDATE ${fq("plaid_transactions")}
     SET is_internal = TRUE,
         category_id = 'internal_transfers',
         subcategory_id = NULL,
         rule_id = NULL,
         categorized_at = CURRENT_TIMESTAMP(),
         updated_at = CURRENT_TIMESTAMP()
     WHERE transaction_id IN UNNEST(@ids)
       AND override_category_id IS NULL
       AND (
         IFNULL(is_internal, FALSE) IS NOT TRUE
         OR IFNULL(category_id, '') != 'internal_transfers'
       )`,
    { ids },
  );
  return ids.length;
}

/** Operator override — null,null clears override (rule result restored on next reapply). */
export async function setPlaidTransactionOverride(
  transactionId: string,
  overrideCategoryId: string | null,
  overrideSubcategoryId: string | null,
): Promise<void> {
  await mutate(
    `UPDATE ${fq("plaid_transactions")}
     SET override_category_id = @override_category_id,
         override_subcategory_id = @override_subcategory_id,
         updated_at = CURRENT_TIMESTAMP()
     WHERE transaction_id = @transaction_id`,
    {
      transaction_id: transactionId,
      override_category_id: overrideCategoryId,
      override_subcategory_id: overrideSubcategoryId,
    },
    {
      override_category_id: "STRING",
      override_subcategory_id: "STRING",
    },
  );
}

export async function setPlaidTransactionCategory(row: {
  transaction_id: string;
  category_id: string | null;
  subcategory_id: string | null;
  rule_id: string | null;
}): Promise<void> {
  await mutate(
    `UPDATE ${fq("plaid_transactions")}
     SET category_id = @category_id,
         subcategory_id = @subcategory_id,
         rule_id = @rule_id,
         categorized_at = CURRENT_TIMESTAMP(),
         updated_at = CURRENT_TIMESTAMP()
     WHERE transaction_id = @transaction_id
       AND override_category_id IS NULL`,
    {
      transaction_id: row.transaction_id,
      category_id: row.category_id,
      subcategory_id: row.subcategory_id,
      rule_id: row.rule_id,
    },
    {
      category_id: "STRING",
      subcategory_id: "STRING",
      rule_id: "STRING",
    },
  );
}

export interface PlaidAccountWrite {
  account_id: string;
  item_id: string;
  name: string | null;
  mask: string | null;
  type: string | null;
  subtype: string | null;
}

/** Upsert one Plaid account (mask = last-4 digits for console display). */
export async function upsertPlaidAccount(row: PlaidAccountWrite): Promise<void> {
  await mutate(
    `MERGE ${fq("plaid_accounts")} T
     USING (SELECT @account_id AS account_id) S
     ON T.account_id = S.account_id
     WHEN MATCHED THEN UPDATE SET
       item_id = @item_id, name = @name, mask = @mask,
       type = @type, subtype = @subtype, updated_at = CURRENT_TIMESTAMP()
     WHEN NOT MATCHED THEN INSERT (
       account_id, item_id, name, mask, type, subtype, updated_at
     ) VALUES (
       @account_id, @item_id, @name, @mask, @type, @subtype, CURRENT_TIMESTAMP()
     )`,
    {
      account_id: row.account_id,
      item_id: row.item_id,
      name: row.name,
      mask: row.mask,
      type: row.type,
      subtype: row.subtype,
    },
    {
      name: "STRING",
      mask: "STRING",
      type: "STRING",
      subtype: "STRING",
    },
  );
}

export type UsageDayOverrideMode = "force_include" | "force_exclude";

export type UsageDayOverridePreview = {
  item: string;
  date: string;
  mode: UsageDayOverrideMode | null;
  high_bar: number | null;
  similar_tomorrow_passes: boolean | null;
  status: string | null;
  reason: string | null;
  delta: number | null;
};

/** MERGE sticky per-day override (Issue #194). */
export async function setUsageDayOverride(
  store: string,
  item: string,
  submittedDate: string,
  mode: UsageDayOverrideMode,
  by: string,
  note?: string,
): Promise<void> {
  await mutate(
    `MERGE ${fq("inventory_usage_day_overrides")} T
     USING (SELECT @store AS store, @item AS item, @date AS submitted_date) S
     ON T.store = S.store AND T.item = S.item AND T.submitted_date = S.submitted_date
     WHEN MATCHED THEN UPDATE SET
       mode = @mode, note = @note, updated_by = @by, updated_at = CURRENT_TIMESTAMP()
     WHEN NOT MATCHED THEN INSERT
       (store, item, submitted_date, mode, note, updated_by, updated_at)
       VALUES (@store, @item, @date, @mode, @note, @by, CURRENT_TIMESTAMP())`,
    {
      store,
      item,
      date: dateParam(submittedDate),
      mode,
      note: note ?? null,
      by,
    },
    { note: "STRING" },
  );
}

/** Clear override → rule-only eligibility (Issue #194). */
export async function clearUsageDayOverride(
  store: string,
  item: string,
  submittedDate: string,
): Promise<void> {
  await mutate(
    `DELETE FROM ${fq("inventory_usage_day_overrides")}
     WHERE store = @store AND item = @item AND submitted_date = @date`,
    { store, item, date: dateParam(submittedDate) },
  );
}

/**
 * Sticky Current Qty override (Issue #240). COALESCE'd in
 * vw_inventory_order_assistant; rematerialize reco after write.
 */
export async function setCurrentQtyOverride(
  store: string,
  item: string,
  quantityUnits: number,
  by: string,
  opts: RecoRefreshOpts = {},
): Promise<void> {
  const trimmed = item.trim();
  if (!trimmed || trimmed === "TOTAL" || trimmed === "Blade") {
    throw new Error(`setCurrentQtyOverride: invalid item '${item}'`);
  }
  if (!Number.isFinite(quantityUnits) || quantityUnits < 0) {
    throw new Error(`setCurrentQtyOverride: quantity must be ≥ 0 (got ${quantityUnits})`);
  }
  await mutate(
    `MERGE ${fq("inventory_current_qty_overrides")} T
     USING (SELECT @store AS store, @item AS item) S
     ON T.store = S.store AND T.item = S.item
     WHEN MATCHED THEN UPDATE SET
       quantity_units = @qty, updated_by = @by, updated_at = CURRENT_TIMESTAMP()
     WHEN NOT MATCHED THEN INSERT
       (store, item, quantity_units, updated_by, updated_at)
       VALUES (@store, @item, @qty, @by, CURRENT_TIMESTAMP())`,
    { store, item: trimmed, qty: quantityUnits, by },
  );
  if (!opts.skipRefresh) await refreshOrderReco(store);
}

/** Drop Current Qty override → ClickUp closing reading wins again. */
export async function clearCurrentQtyOverride(
  store: string,
  item: string,
  opts: RecoRefreshOpts = {},
): Promise<void> {
  const trimmed = item.trim();
  if (!trimmed || trimmed === "TOTAL" || trimmed === "Blade") {
    throw new Error(`clearCurrentQtyOverride: invalid item '${item}'`);
  }
  await mutate(
    `DELETE FROM ${fq("inventory_current_qty_overrides")}
     WHERE store = @store AND item = @item`,
    { store, item: trimmed },
  );
  if (!opts.skipRefresh) await refreshOrderReco(store);
}

/** Batch MERGE Current Qty overrides, then one reco refresh (Issue #240). */
export async function applyCurrentQtyOverrides(
  store: string,
  rows: { item: string; quantityUnits: number }[],
  by: string,
  opts: RecoRefreshOpts = {},
): Promise<void> {
  for (const r of rows) {
    await setCurrentQtyOverride(store, r.item, r.quantityUnits, by, { skipRefresh: true });
  }
  if (!opts.skipRefresh) await refreshOrderReco(store);
}

/** Clear overrides for many items, then one reco refresh. */
export async function clearCurrentQtyOverrides(
  store: string,
  items: string[],
  opts: RecoRefreshOpts = {},
): Promise<void> {
  for (const item of items) {
    await clearCurrentQtyOverride(store, item, { skipRefresh: true });
  }
  if (!opts.skipRefresh) await refreshOrderReco(store);
}

/** Read one audit row after override for threshold preview. */
export async function readUsageDayAuditRow(
  store: string,
  item: string,
  submittedDate: string,
): Promise<UsageDayOverridePreview | null> {
  const rows = await q<{
    item: string;
    submitted_date: string;
    override_mode: string | null;
    high_bar: number | null;
    similar_tomorrow_passes: boolean | null;
    status: string | null;
    reason: string | null;
    delta: number | null;
  }>(
    `SELECT item, CAST(submitted_date AS STRING) AS submitted_date,
       override_mode, high_bar, similar_tomorrow_passes, status, reason, delta
     FROM ${fq("vw_inventory_usage_day_audit")}
     WHERE store = @store AND item = @item AND submitted_date = @date
     LIMIT 1`,
    { store, item, date: dateParam(submittedDate) },
  );
  if (!rows.length) return null;
  const r = rows[0];
  return {
    item: r.item,
    date: r.submitted_date,
    mode: (r.override_mode as UsageDayOverrideMode | null) ?? null,
    high_bar: r.high_bar,
    similar_tomorrow_passes: r.similar_tomorrow_passes,
    status: r.status,
    reason: r.reason,
    delta: r.delta,
  };
}

// ── Automations (Issue #216) ───────────────────────────────────────────

export type AutomationUpsert = {
  enabled: boolean;
  days_of_week: number[];
  hour_local: number;
  minute_local: number;
  timezone: string;
  destination: "dm" | "channel";
  channel_id: string;
  dm_user_id: string;
  workspace_id: string;
  template: string;
};

export async function upsertAutomation(
  store: string,
  automationId: string,
  cfg: AutomationUpsert,
  by: string,
): Promise<void> {
  await mutate(
    `MERGE ${fq("automations")} T
     USING (SELECT @store AS store, @id AS automation_id) S
     ON T.store = S.store AND T.automation_id = S.automation_id
     WHEN MATCHED THEN UPDATE SET
       enabled = @enabled,
       days_of_week = @days,
       hour_local = @hour,
       minute_local = @minute,
       timezone = @tz,
       destination = @destination,
       channel_id = @channel_id,
       dm_user_id = @dm_user_id,
       workspace_id = @workspace_id,
       template = @template,
       updated_at = CURRENT_TIMESTAMP(),
       updated_by = @by
     WHEN NOT MATCHED THEN INSERT (
       store, automation_id, enabled, days_of_week, hour_local, minute_local,
       timezone, destination, channel_id, dm_user_id, workspace_id, template,
       updated_at, updated_by
     ) VALUES (
       @store, @id, @enabled, @days, @hour, @minute,
       @tz, @destination, @channel_id, @dm_user_id, @workspace_id, @template,
       CURRENT_TIMESTAMP(), @by
     )`,
    {
      store,
      id: automationId,
      enabled: cfg.enabled,
      days: JSON.stringify(cfg.days_of_week),
      hour: intParam(cfg.hour_local),
      minute: intParam(cfg.minute_local),
      tz: cfg.timezone,
      destination: cfg.destination,
      channel_id: cfg.channel_id,
      dm_user_id: cfg.dm_user_id,
      workspace_id: cfg.workspace_id,
      template: cfg.template,
      by,
    },
  );
}

export async function insertAutomationPost(row: {
  store: string;
  automation_id: string;
  post_date_ct: string;
  destination: string;
  channel_id: string | null;
  message_id: string | null;
  content: string;
  dry_run: boolean;
  trigger: string;
  updated_by: string;
}): Promise<void> {
  await mutate(
    `INSERT INTO ${fq("automation_posts")} (
       store, automation_id, post_date_ct, posted_at, destination, channel_id,
       message_id, content, dry_run, trigger, updated_by
     ) VALUES (
       @store, @id, @post_date, CURRENT_TIMESTAMP(), @destination, @channel_id,
       @message_id, @content, @dry_run, @trigger, @by
     )`,
    {
      store: row.store,
      id: row.automation_id,
      post_date: dateParam(row.post_date_ct),
      destination: row.destination,
      channel_id: row.channel_id,
      message_id: row.message_id,
      content: row.content,
      dry_run: row.dry_run,
      trigger: row.trigger,
      by: row.updated_by,
    },
  );
}

export async function hasAutomationPostToday(
  store: string,
  automationId: string,
  postDateCt: string,
): Promise<boolean> {
  const rows = await q<{ n: number }>(
    `SELECT COUNT(1) AS n FROM ${fq("automation_posts")}
     WHERE store = @store AND automation_id = @id
       AND post_date_ct = @d AND dry_run = FALSE`,
    { store, id: automationId, d: dateParam(postDateCt) },
  );
  return Number(rows[0]?.n ?? 0) > 0;
}
