"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail } from "@/lib/auth/identity";
import { currentStore } from "@/lib/config/current-store";
import { upsertGoal, GOAL_KEYS, type GoalKey } from "@/lib/bq/writes";
import { asAck, type ActionAck } from "@/lib/actions/types";
import { actionDenied } from "@/lib/access/server";

/** Saves every edited goal field from the Goals drawer in one submission. */
export async function saveGoalsAction(
  values: Partial<Record<GoalKey, string>>,
): Promise<ActionAck> {
  const store = await currentStore();
  const denied = await actionDenied("saveGoalsAction");
  if (denied) return denied;
  return asAck(async () => {
    const by = await operatorEmail();
    const entries = GOAL_KEYS.filter((k) => values[k] !== undefined && values[k] !== "");
    await Promise.all(entries.map((k) => upsertGoal(store, k, values[k]!, by)));
    revalidatePath("/home");
    revalidatePath("/labor");
  }, "Goals saved.");
}

/**
 * Saves a single goal field from the Home scorecard's inline pencil-edit.
 * `value` must already be in storage units (a fraction for percent goals —
 * see lib/kpi/goal-fields.ts's percentInputToFraction, applied client-side
 * before this is called) — this is a thin single-key wrapper over the same
 * `upsertGoal` the bulk GoalsDrawer uses, not a second write path.
 */
export async function saveGoalAction(key: GoalKey, value: string): Promise<ActionAck> {
  const store = await currentStore();
  const denied = await actionDenied("saveGoalAction");
  if (denied) return denied;
  return asAck(async () => {
    const by = await operatorEmail();
    await upsertGoal(store, key, value, by);
    revalidatePath("/home");
    revalidatePath("/labor");
  }, "Goal saved.");
}
