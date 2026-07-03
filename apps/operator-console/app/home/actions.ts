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
