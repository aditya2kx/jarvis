"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import {
  addTrainingShift,
  addRecognitionBonus,
  addEmployeePerk,
  applyTipExemptions,
  type TipExemptionDraft,
} from "@/lib/bq/writes";
import { triggerModelRecompute } from "@/lib/bhaga/recompute";
import { failAck, okAck, type ActionAck } from "@/lib/actions/types";
import { listPayPeriodsWithPaidStatus } from "@/lib/bq/queries";
import {
  pollPayrollDraft,
  startPayrollDraft,
} from "@/lib/bhaga/payroll-draft";

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

const PERK_IDS = new Set(["mileage", "gym", "food_handler", "other"]);

export async function addEmployeePerkAction(
  employee: string,
  perkId: string,
  amountDollars: number,
  payPeriod: string,
  cadence: "once" | "biweekly",
  note: string,
): Promise<ActionAck> {
  try {
    if (!FEATURES.writePerks) throw new Error("Reimbursement write path is disabled");
    if (!PERK_IDS.has(perkId)) throw new Error("Unknown reimbursement type");
    const by = await operatorEmail();
    const amountCents = Math.round(amountDollars * 100);
    const storedPeriod = cadence === "biweekly" ? "" : payPeriod;
    if (cadence === "once" && !storedPeriod.trim()) {
      throw new Error("Pay period is required for one-time reimbursements");
    }
    const desc = note.trim() || "Misc reimbursement";
    await addEmployeePerk(
      DEFAULT_STORE,
      employee,
      perkId,
      amountCents,
      storedPeriod,
      cadence,
      desc,
      by,
    );
    revalidatePath("/payroll");
    return okAck({ message: "Reimbursement added." });
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

/** Start ADP Preview for an unpaid period. Leaves draft; never Approve/Save. */
export async function runPayrollDraftAction(
  periodStart: string,
  periodEnd: string,
): Promise<ActionAck<{ executionName?: string; mode: "local" | "cloud" }>> {
  try {
    if (!FEATURES.adpPayrollDraft) {
      throw new Error("ADP Preview draft is disabled (FEATURES.adpPayrollDraft)");
    }
    const periods = await listPayPeriodsWithPaidStatus(6);
    const opt = periods.find(
      (p) => p.period_start === periodStart && p.period_end === periodEnd,
    );
    if (!opt) throw new Error("Unknown pay period");
    if (!opt.unpaid || opt.submitted) {
      throw new Error("ADP Preview is only for unpaid periods that are not yet submitted");
    }
    const started = await startPayrollDraft(
      DEFAULT_STORE,
      periodStart,
      periodEnd,
    );
    return okAck({
      data: { executionName: started.executionName, mode: started.mode },
      queued: ["adp-payroll-draft"],
      message: started.message,
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Poll local status file, Cloud Run execution, and BQ Preview totals. */
export async function pollPayrollDraftAction(opts: {
  executionName?: string | null;
  mode?: "local" | "cloud" | null;
  periodStart: string;
  periodEnd: string;
}): Promise<
  ActionAck<{
    done: boolean;
    succeeded: boolean | null;
    failed: boolean;
    message: string | null;
    previewHours?: number | null;
    previewGross?: number | null;
  }>
> {
  try {
    if (!FEATURES.adpPayrollDraft) {
      throw new Error("ADP Preview draft is disabled (FEATURES.adpPayrollDraft)");
    }
    const status = await pollPayrollDraft({
      executionName: opts.executionName,
      mode: opts.mode,
      periodStart: opts.periodStart,
      periodEnd: opts.periodEnd,
    });
    return okAck({ data: status });
  } catch (e) {
    return failAck(e);
  }
}
