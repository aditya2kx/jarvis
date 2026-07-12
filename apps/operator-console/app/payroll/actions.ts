"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import { addTrainingShift, addRecognitionBonus } from "@/lib/bq/writes";

export async function addTrainingShiftAction(employeeName: string, date: string, note: string) {
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
