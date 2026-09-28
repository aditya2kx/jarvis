import "server-only";
import { dateParam, fq, mutate } from "./client";

/**
 * Append-only audit of operator edits to order-reco inputs (Issue #350,
 * migration 080). Each row is the entity's full state after the edit; the
 * previous state is the prior row for the same key (vw_inventory_edit_log).
 * Mirrors cloud/webhook/handler.py _log_inventory_edit so console and Slack
 * edits land in one log.
 */
export type InventoryEditEntity =
  | "order_tub_pins"
  | "restock_actuals"
  | "restock_schedule"
  | "store_config"
  | "usage_day_override"
  | "current_qty_override";

export type InventoryEdit = {
  store: string;
  entity: InventoryEditEntity;
  action: "set" | "clear";
  deliveryDate?: string | null;
  item?: string | null;
  key?: string | null;
  newValue?: unknown;
  by: string;
};

/**
 * Best effort: the edit itself has already been written, so a log failure
 * must not report the save as failed. Leaves a greppable breadcrumb instead.
 */
export async function logInventoryEdit(e: InventoryEdit): Promise<void> {
  try {
    await mutate(
      `INSERT INTO ${fq("inventory_edit_log")}
         (event_id, store, entity, action, delivery_date, item, key, new_value, edited_by, edited_at, source)
       VALUES (GENERATE_UUID(), @store, @entity, @action, @date, @item, @key,
               SAFE.PARSE_JSON(@value), @by, CURRENT_TIMESTAMP(), 'console')`,
      {
        store: e.store,
        entity: e.entity,
        action: e.action,
        date: e.deliveryDate ? dateParam(e.deliveryDate) : null,
        item: e.item ?? null,
        key: e.key ?? null,
        value: e.newValue === undefined ? null : JSON.stringify(e.newValue),
        by: e.by,
      },
      { date: "DATE", item: "STRING", key: "STRING", value: "STRING" },
    );
  } catch (err) {
    console.error(
      `inventory_edit_log_failed store=${e.store} entity=${e.entity} action=${e.action} ` +
        `date=${e.deliveryDate ?? ""} item=${e.item ?? ""} key=${e.key ?? ""}: ${String(err)}`,
    );
  }
}
