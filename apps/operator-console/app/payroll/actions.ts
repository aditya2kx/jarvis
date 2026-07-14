"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import {
  addTrainingShift,
  addRecognitionBonus,
  applyTipExemptions,
  type TipExemptionDraft,
} from "@/lib/bq/writes";
import { triggerModelRecompute } from "@/lib/bhaga/recompute";

export async function addTrainingShiftAction(employeeName: string, date: string, note: string) {
  if (!FEATURES.writeTraining) throw new Error("Training quick-add is disabled");
  const by = await operatorEmail();
  await addTrainingShift(DEFAULT_STORE, employeeName, date, by, note);
  revalidatePath("/payroll");
}

/** amountDollars is the drawer's user-facing input; converted to integer cents at the boundary. */
export async function addRecognitionBonusAction(
  payPeriod: string,
  employee: string,
  amountDollars: number,
  reason: string,
) {
  const by = await operatorEmail();
  const amountCents = Math.round(amountDollars * 100);
  await addRecognitionBonus(DEFAULT_STORE, payPeriod, employee, amountCents, reason, by);
  revalidatePath("/payroll");
}

/** Batch tip-exemption Update (Issue #167) — writes BQ then recomputes touched dates. */
export async function applyTipExemptionsAction(drafts: TipExemptionDraft[]) {
  if (!FEATURES.writeTipExemptions) {
    throw new Error("Tip exemptions write path is disabled (FEATURES.writeTipExemptions)");
  }
  if (!drafts.length) return { recomputed: [] as string[] };
  const by = await operatorEmail();
  await applyTipExemptions(DEFAULT_STORE, drafts, by);
  const dates = [...new Set(drafts.map((d) => d.date))];
  await triggerModelRecompute(dates);
  revalidatePath("/payroll");
  return { recomputed: dates };
}
