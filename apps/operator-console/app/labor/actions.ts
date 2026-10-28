"use server";

import { failAck, okAck, type ActionAck } from "@/lib/actions/types";
import { FEATURES } from "@/lib/config/features";
import { DEFAULT_STORE, operatorEmail } from "@/lib/auth/identity";
import { revalidatePath } from "next/cache";
import { punchGapFor } from "@/lib/bq/queries";
import { recordPunchGapDecision } from "@/lib/bq/writes";
import {
  buildPunchGaps,
  formatClock,
  validateDecision,
  type DecisionInput,
} from "@/lib/labor/punch-gaps";
import { saveScheduleRules } from "@/lib/labor/schedule-rules-store";
import { queueDraftPush, weekPushRows } from "@/lib/labor/schedule-push-store";
import { validatePushShifts, type PushRow } from "@/lib/labor/schedule-push";
import { startScheduleWrite } from "@/lib/bhaga/schedule-write";
import { validateAdpChanges, type ApplyRow } from "@/lib/labor/adp-changes";
import { applyRows, queueAdpChanges } from "@/lib/labor/adp-change-store";
import { isConsolePreview } from "@/lib/config/preview";
import { runningAdpExecution } from "@/lib/bhaga/recompute";
import { chicagoTodayIso } from "@/lib/filters/range";
import {
  pollAdpSync,
  runningAdpSync,
  startAdpSync,
  type AdpSyncPoll,
  type AdpSyncStart,
} from "@/lib/bhaga/adp-sync";
import {
  pollUnavailabilityApprove,
  startUnavailabilityApprove,
  type UnavailabilityApprovePoll,
} from "@/lib/bhaga/unavailability-approve";
import {
  pollPunchFixApply,
  startPunchFixApply,
  type PunchFixPoll,
  type PunchFixStart,
} from "@/lib/bhaga/punch-fix";
import { ACCESS_DENIED_MESSAGE, actionDenied, holdsAccess } from "@/lib/access/server";

/** Append a new scheduling-rules version (older versions stay as history). */
export async function saveScheduleRulesAction(
  rules: unknown,
  baseVersion: number,
  note?: string,
): Promise<ActionAck<{ version: number }>> {
  const denied = await actionDenied("saveScheduleRulesAction");
  if (denied) return denied;
  try {
    const by = await operatorEmail();
    const version = await saveScheduleRules(DEFAULT_STORE, rules, baseVersion, by, note?.trim() || undefined);
    revalidatePath("/labor");
    return okAck({ data: { version }, message: `Rules saved (version ${version}).` });
  } catch (e) {
    return failAck(e);
  }
}

function requireScheduleWrite(): void {
  if (!FEATURES.adpScheduleWrite) {
    throw new Error("Saving shifts to ADP is turned off (CONSOLE_ADP_SCHEDULE_WRITE).");
  }
}

/** Queue the week's draft shifts and start the ADP job that saves them as ADP drafts. */
export async function saveDraftsToAdpAction(
  weekStart: string,
  shifts: unknown,
): Promise<ActionAck<{ pushId: string; count: number }>> {
  const denied = await actionDenied("saveDraftsToAdpAction");
  if (denied) return denied;
  try {
    requireScheduleWrite();
    const valid = validatePushShifts(weekStart, shifts, chicagoTodayIso());
    const pushId = await queueDraftPush(DEFAULT_STORE, weekStart, valid, await operatorEmail());
    await startScheduleWrite(DEFAULT_STORE, { mode: "drafts", pushId });
    // The week now has a saved plan; re-render so it locks to what was just sent.
    revalidatePath("/labor");
    return okAck({
      data: { pushId, count: valid.length },
      queued: ["adp-schedule-write"],
      message: `Saving ${valid.length} shifts to ADP as drafts — about 7 s each. Employees can't see drafts until you publish.`,
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Publish the week's ADP drafts (employees are notified in ADP Mobile). */
export async function publishWeekAction(weekStart: string): Promise<ActionAck<{ weekStart: string }>> {
  const denied = await actionDenied("publishWeekAction");
  if (denied) return denied;
  try {
    requireScheduleWrite();
    const rows = await weekPushRows(DEFAULT_STORE, weekStart);
    if (!rows.some((r) => r.status === "drafted")) {
      throw new Error("Nothing saved to ADP for this week yet — save drafts first.");
    }
    await startScheduleWrite(DEFAULT_STORE, { mode: "publish", weekStart });
    return okAck({
      data: { weekStart },
      queued: ["adp-schedule-write"],
      message: "Publishing the week in ADP — employees are notified in ADP Mobile.",
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Re-read the week's open shifts from ADP, then DM the operator or post to Shift Coverage. */
export async function sendPublishMessageAction(
  weekStart: string,
  to: "dm" | "channel" = "dm",
): Promise<ActionAck<{ weekStart: string }>> {
  const denied = await actionDenied("sendPublishMessageAction");
  if (denied) return denied;
  try {
    requireScheduleWrite();
    if (to !== "dm" && to !== "channel") {
      throw new Error("Choose a DM or Shift Coverage.");
    }
    const rows = await weekPushRows(DEFAULT_STORE, weekStart);
    if (!rows.some((r) => r.status === "published")) {
      throw new Error("This week isn't published yet — publish it first.");
    }
    await startScheduleWrite(DEFAULT_STORE, { mode: "dm", weekStart, to });
    return okAck({
      data: { weekStart },
      queued: ["adp-schedule-write"],
      message:
        to === "channel"
          ? "Re-reading open shifts from ADP, then posting to Shift Coverage (about 3 min)."
          : "Re-reading open shifts from ADP, then sending you the ClickUp DM (about 3 min).",
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Latest ADP push state for a week (poll target for every step). */
export type ScheduleWriteRun = {
  mode: "drafts" | "publish" | "dm";
  pushId?: string;
  weekStart?: string;
  to?: "dm" | "channel";
};

/** The week's push rows plus the ADP schedule-write job still running, if any (survives a reload). */
export async function schedulePushStatusAction(
  weekStart: string,
): Promise<ActionAck<{ rows: PushRow[]; running: ScheduleWriteRun | null }>> {
  const denied = await actionDenied("schedulePushStatusAction");
  if (denied) return denied;
  try {
    const [rows, hit] = await Promise.all([
      weekPushRows(DEFAULT_STORE, weekStart),
      runningAdpExecution("BHAGA_ADP_SCHEDULE_WRITE"),
    ]);
    const mode = hit?.env.BHAGA_ADP_SCHEDULE_WRITE;
    const running: ScheduleWriteRun | null =
      hit && (mode === "drafts" || mode === "publish" || mode === "dm")
        ? {
            mode,
            pushId: hit.env.BHAGA_SCHEDULE_PUSH_ID,
            weekStart: hit.env.BHAGA_SCHEDULE_WEEK_START,
            to: hit.env.BHAGA_SCHEDULE_MESSAGE_TO === "channel" ? "channel" : "dm",
          }
        : null;
    return okAck({ data: { rows, running } });
  } catch (e) {
    return failAck(e);
  }
}

/**
 * "Changes to ADP" Apply (Issue #434): every staged shift and unavailability change in one
 * ADP run. Shift changes need Coverage access + CONSOLE_ADP_SCHEDULE_WRITE; unavailability
 * changes need Availability access + CONSOLE_ADP_UNAVAIL_WRITE.
 */
export async function applyAdpChangesAction(
  changes: unknown,
): Promise<ActionAck<{ batchId: string; ids: string[] }>> {
  const denied = await actionDenied("applyAdpChangesAction");
  if (denied) return denied;
  try {
    if (isConsolePreview()) throw new Error("Read-only preview: nothing is sent to ADP from here.");
    const valid = validateAdpChanges(changes, chicagoTodayIso());
    const hasShifts = valid.some((a) => a.area === "shift");
    const hasUnavail = valid.some((a) => a.area === "unavailability");
    if (hasShifts) {
      requireScheduleWrite();
      if (!(await holdsAccess("labor:coverage"))) throw new Error(ACCESS_DENIED_MESSAGE);
    }
    if (hasUnavail) {
      if (!FEATURES.adpUnavailabilityWrite) {
        throw new Error("Unavailability changes to ADP are turned off (CONSOLE_ADP_UNAVAIL_WRITE).");
      }
      if (!(await holdsAccess("labor:availability"))) throw new Error(ACCESS_DENIED_MESSAGE);
    }
    if (await runningAdpExecution("BHAGA_ADP_SCHEDULE_WRITE")) {
      throw new Error("ADP is busy with another schedule write — try again when it finishes.");
    }
    const { batchId, ids } = await queueAdpChanges(DEFAULT_STORE, valid, await operatorEmail());
    await startScheduleWrite(DEFAULT_STORE, { mode: "apply", batchId });
    return okAck({
      data: { batchId, ids },
      queued: ["adp-schedule-write"],
      message: `Applying ${valid.length} change${valid.length === 1 ? "" : "s"} in ADP — about 2 min to sign in, then ~20 s each.`,
    });
  } catch (e) {
    return failAck(e);
  }
}

/** An Apply's rows, plus the Apply still running on Cloud Run (so a reload resumes it). */
export async function adpChangesStatusAction(
  batchId: string | null,
): Promise<ActionAck<{ rows: ApplyRow[]; running: string | null }>> {
  const denied = await actionDenied("adpChangesStatusAction");
  if (denied) return denied;
  try {
    const hit = await runningAdpExecution("BHAGA_ADP_SCHEDULE_WRITE", "apply");
    const running = hit?.env.BHAGA_SCHEDULE_BATCH_ID || null;
    const id = batchId ?? running;
    return okAck({ data: { rows: id ? await applyRows(id) : [], running } });
  } catch (e) {
    return failAck(e);
  }
}

/** Approve one pending ADP unavailability request (headless Cloud Run job). */
export async function approveUnavailabilityAction(
  rowKey: string,
): Promise<ActionAck<{ executionName: string }>> {
  const denied = await actionDenied("approveUnavailabilityAction");
  if (denied) return denied;
  try {
    if (!FEATURES.adpUnavailabilityApprove) {
      throw new Error("Approving in ADP is turned off (CONSOLE_ADP_UNAVAIL_APPROVE).");
    }
    if (typeof rowKey !== "string" || !rowKey) throw new Error("Missing request.");
    const data = await startUnavailabilityApprove(DEFAULT_STORE, rowKey, await operatorEmail());
    return okAck({
      data,
      queued: ["adp-unavail-approve"],
      message: "Approving in ADP — about 5–10 min including the schedule refresh.",
    });
  } catch (e) {
    return failAck(e);
  }
}

export async function pollUnavailabilityApproveAction(opts: {
  rowKey: string;
  executionName?: string | null;
}): Promise<ActionAck<UnavailabilityApprovePoll>> {
  const denied = await actionDenied("pollUnavailabilityApproveAction");
  if (denied) return denied;
  try {
    return okAck({ data: await pollUnavailabilityApprove(opts) });
  } catch (e) {
    return failAck(e);
  }
}

/** Start "Sync ADP" — every ADP read in one Cloud Run login. */
export async function syncAdpAction(targetDate: string): Promise<ActionAck<AdpSyncStart>> {
  const denied = await actionDenied("syncAdpAction");
  if (denied) return denied;
  try {
    const data = await startAdpSync(DEFAULT_STORE, targetDate);
    return okAck({ data, queued: ["adp-sync"], message: data.message });
  } catch (e) {
    return failAck(e);
  }
}

/** A "Sync ADP" still running on Cloud Run, so a reloaded page resumes its status. */
export async function runningAdpSyncAction(): Promise<
  ActionAck<{ executionName: string; baselineScrapedAt: string } | null>
> {
  const denied = await actionDenied("runningAdpSyncAction");
  if (denied) return denied;
  try {
    return okAck({ data: await runningAdpSync() });
  } catch (e) {
    return failAck(e);
  }
}

/** Poll BQ adp_shifts scraped_at + the Cloud Run execution. */
export async function pollAdpSyncAction(opts: {
  baselineScrapedAt: string | null;
  executionName?: string | null;
}): Promise<ActionAck<AdpSyncPoll>> {
  const denied = await actionDenied("pollAdpSyncAction");
  if (denied) return denied;
  try {
    const data = await pollAdpSync(opts);
    return okAck({ data });
  } catch (e) {
    return failAck(e);
  }
}

/**
 * Record Accept / Edit / Reject on one open punch (Issue #356). Validates
 * against the stored gap, not the client's copy, so a stale page cannot write
 * an Out before the clock-in or decide a gap ADP no longer has.
 */
export async function decidePunchGapAction(
  input: DecisionInput,
): Promise<ActionAck<{ decisionId: string }>> {
  const denied = await actionDenied("decidePunchGapAction");
  if (denied) return denied;
  try {
    const row = await punchGapFor(DEFAULT_STORE, input.date, input.employee);
    if (!row) throw new Error("This gap is no longer open in ADP — refresh the page.");
    const gap = buildPunchGaps([row], [])[0]!;
    const problem = validateDecision(gap, input);
    if (problem) throw new Error(problem);
    const outTime =
      input.action === "accept" ? gap.suggestedOut : input.action === "edit" ? input.outTime! : null;
    const inTime =
      gap.kind !== "no_entry" || input.action === "reject"
        ? null
        : input.action === "accept"
          ? gap.suggestedIn
          : input.inTime!;
    const decisionId = await recordPunchGapDecision({
      store: DEFAULT_STORE,
      date: input.date,
      employee_id: input.employee,
      action: input.action,
      in_time: inTime,
      out_time: outTime,
      open_entry_index: gap.openIndex,
      note: input.note?.trim() || null,
      status: input.action === "reject" ? "rejected" : "recorded",
      decided_by: await operatorEmail(),
    });
    const message =
      input.action === "reject"
        ? `Dismissed — ${input.employee}, ${input.date}.`
        : `Saved ${input.employee}, ${input.date}: ${inTime ? `${formatClock(inTime)} – ` : "out "}${formatClock(outTime)}.`;
    return okAck({ data: { decisionId }, message });
  } catch (e) {
    return failAck(e);
  }
}

/**
 * Accept the suggestion on every listed gap (the rows the operator filtered to).
 * Each one is validated against its stored gap exactly like a single Accept;
 * rows without a valid suggestion are skipped and reported, not guessed.
 * Records decisions only — nothing reaches ADP until "Write to ADP".
 */
export async function acceptPunchGapsAction(
  items: { date: string; employee: string }[],
): Promise<ActionAck<{ accepted: number; skipped: string[] }>> {
  const denied = await actionDenied("acceptPunchGapsAction");
  if (denied) return denied;
  try {
    const by = await operatorEmail();
    let accepted = 0;
    const skipped: string[] = [];
    for (const { date, employee } of items) {
      const row = await punchGapFor(DEFAULT_STORE, date, employee);
      const gap = row ? buildPunchGaps([row], [])[0]! : null;
      const problem = gap
        ? validateDecision(gap, { action: "accept" })
        : "no longer open in ADP";
      if (!gap || problem) {
        skipped.push(`${employee} ${date}: ${problem}`);
        continue;
      }
      await recordPunchGapDecision({
        store: DEFAULT_STORE,
        date,
        employee_id: employee,
        action: "accept",
        in_time: gap.kind === "no_entry" ? gap.suggestedIn : null,
        out_time: gap.suggestedOut,
        open_entry_index: gap.openIndex,
        note: null,
        status: "recorded",
        decided_by: by,
      });
      accepted += 1;
    }
    const message =
      `Accepted ${accepted} suggestion${accepted === 1 ? "" : "s"}` +
      (skipped.length ? ` · skipped ${skipped.length}` : "") +
      ". Not in ADP yet — use Write to ADP.";
    return okAck({ data: { accepted, skipped }, message });
  } catch (e) {
    return failAck(e);
  }
}

/**
 * Dismiss every listed gap: the operator says no fix is owed (e.g. a scheduled
 * day the employee did not work). Rows already written or being written to
 * ADP are skipped. Dismissed rows leave the Open list; nothing reaches ADP.
 */
export async function dismissPunchGapsAction(
  items: { date: string; employee: string }[],
): Promise<ActionAck<{ dismissed: number; skipped: string[] }>> {
  const denied = await actionDenied("dismissPunchGapsAction");
  if (denied) return denied;
  try {
    const by = await operatorEmail();
    let dismissed = 0;
    const skipped: string[] = [];
    for (const { date, employee } of items) {
      const row = await punchGapFor(DEFAULT_STORE, date, employee);
      if (!row) {
        skipped.push(`${employee} ${date}: no longer open in ADP`);
        continue;
      }
      if (row.decision_status === "applying" || row.decision_status === "applied") {
        skipped.push(`${employee} ${date}: already ${row.decision_status === "applied" ? "in" : "going to"} ADP`);
        continue;
      }
      await recordPunchGapDecision({
        store: DEFAULT_STORE,
        date,
        employee_id: employee,
        action: "reject",
        in_time: null,
        out_time: null,
        open_entry_index: row.open_entry_index,
        note: null,
        status: "rejected",
        decided_by: by,
      });
      dismissed += 1;
    }
    const message =
      `Dismissed ${dismissed}` + (skipped.length ? ` · skipped ${skipped.length}` : "") + ".";
    return okAck({ data: { dismissed, skipped }, message });
  } catch (e) {
    return failAck(e);
  }
}

/**
 * "Write to ADP" (Issue #356): fill the approved clock-outs into ADP Timecards.
 * Only decisions still `recorded`/`failed` are claimed, so a double click or a
 * stale page cannot write the same entry twice.
 */
export async function writePunchGapsToAdpAction(
  decisionIds: string[],
): Promise<ActionAck<PunchFixStart>> {
  const denied = await actionDenied("writePunchGapsToAdpAction");
  if (denied) return denied;
  try {
    if (!FEATURES.punchFixWriteback) throw new Error("Writing to ADP is switched off.");
    const ids = decisionIds.filter((id) => typeof id === "string" && id.length > 0);
    const data = await startPunchFixApply(DEFAULT_STORE, ids);
    return okAck({ data, queued: ["adp-punch-fix"], message: data.message });
  } catch (e) {
    return failAck(e);
  }
}

/** Poll write-back status for the decisions a "Write to ADP" click claimed. */
export async function pollPunchGapWriteAction(opts: {
  decisionIds: string[];
  executionName?: string | null;
}): Promise<ActionAck<PunchFixPoll>> {
  const denied = await actionDenied("pollPunchGapWriteAction");
  if (denied) return denied;
  try {
    const data = await pollPunchFixApply({ store: DEFAULT_STORE, ...opts });
    return okAck({ data });
  } catch (e) {
    return failAck(e);
  }
}
