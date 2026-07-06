"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import { upsertGoal, GOAL_KEYS, type GoalKey } from "@/lib/bq/writes";

/** Saves every edited goal field from the Goals drawer in one submission. */
export async function saveGoalsAction(values: Partial<Record<GoalKey, string>>) {
  const by = await operatorEmail();
  const entries = GOAL_KEYS.filter((k) => values[k] !== undefined && values[k] !== "");
  await Promise.all(entries.map((k) => upsertGoal(DEFAULT_STORE, k, values[k]!, by)));
  revalidatePath("/home");
}

/**
 * Saves a single goal field from the Home scorecard's inline pencil-edit.
 * `value` must already be in storage units (a fraction for percent goals —
 * see lib/kpi/goal-fields.ts's percentInputToFraction, applied client-side
 * before this is called) — this is a thin single-key wrapper over the same
 * `upsertGoal` the bulk GoalsDrawer uses, not a second write path.
 */
export async function saveGoalAction(key: GoalKey, value: string) {
  const by = await operatorEmail();
  await upsertGoal(DEFAULT_STORE, key, value, by);
  revalidatePath("/home");
}
