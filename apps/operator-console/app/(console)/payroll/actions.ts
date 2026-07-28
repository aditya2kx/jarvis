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
import { failAck, okAck, type ActionAck } from "@/lib/actions/types";

export async function addTrainingShiftAction(
  employeeName: string,
  date: string,
  note: string,
): Promise<ActionAck> {
  try {
    if (!FEATURES.writeTraining) throw new Error("Training quick-add is disabled");
    const by = await operatorEmail();
    await addTrainingShift(DEFAULT_STORE, employeeName, date, by, note);
    revalidatePath("/payroll");
    return okAck({ message: "Training shift added." });
  } catch (e) {
    return failAck(e);
  }
}

/** amountDollars is the drawer's user-facing input; converted to integer cents at the boundary. */
export async function addRecognitionBonusAction(
  payPeriod: string,
  employee: string,
  amountDollars: number,
  reason: string,
): Promise<ActionAck> {
  try {
    const by = await operatorEmail();
    const amountCents = Math.round(amountDollars * 100);
    await addRecognitionBonus(DEFAULT_STORE, payPeriod, employee, amountCents, reason, by);
    revalidatePath("/payroll");
    return okAck({ message: "Recognition bonus added." });
  } catch (e) {
    return failAck(e);
  }
}

/** Batch tip-exemption Update (Issue #167) — writes BQ then recomputes touched dates. */
export async function applyTipExemptionsAction(
  drafts: TipExemptionDraft[],
): Promise<ActionAck<{ recomputed: string[] }>> {
  try {
    if (!FEATURES.writeTipExemptions) {
      throw new Error("Tip exemptions write path is disabled (FEATURES.writeTipExemptions)");
    }
    if (!drafts.length) return okAck({ data: { recomputed: [] }, message: "No changes." });
    const by = await operatorEmail();
    await applyTipExemptions(DEFAULT_STORE, drafts, by);
    // One FORCE_MODEL job rematerializes tip alloc for all touched dates —
    // do not fire N concurrent jobs (races + Slack tip-pool failure spam).
    const recomputed = await triggerModelRecompute(drafts.map((d) => d.date));
    revalidatePath("/payroll");
    return okAck({
      data: { recomputed },
      queued: recomputed.length ? ["model-recompute"] : undefined,
      message: recomputed.length
        ? `Updated ${drafts.length} exemption(s); model recompute queued for ${recomputed.join(", ")}.`
        : `Updated ${drafts.length} exemption(s).`,
    });
  } catch (e) {
    return failAck(e);
  }
}
