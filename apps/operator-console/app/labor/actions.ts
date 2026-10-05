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

/** Append a new scheduling-rules version (older versions stay as history). */
export async function saveScheduleRulesAction(
  rules: unknown,
  baseVersion: number,
  note?: string,
): Promise<ActionAck<{ version: number }>> {
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

/** Latest ADP push state for a week (poll target for both steps). */
export type ScheduleWriteRun = { mode: "drafts" | "publish"; pushId?: string; weekStart?: string };

/** The week's push rows plus the ADP schedule-write job still running, if any (survives a reload). */
export async function schedulePushStatusAction(
  weekStart: string,
): Promise<ActionAck<{ rows: PushRow[]; running: ScheduleWriteRun | null }>> {
  try {
    const [rows, hit] = await Promise.all([
      weekPushRows(DEFAULT_STORE, weekStart),
      runningAdpExecution("BHAGA_ADP_SCHEDULE_WRITE"),
    ]);
    const mode = hit?.env.BHAGA_ADP_SCHEDULE_WRITE;
    const running: ScheduleWriteRun | null =
      hit && (mode === "drafts" || mode === "publish")
        ? { mode, pushId: hit.env.BHAGA_SCHEDULE_PUSH_ID, weekStart: hit.env.BHAGA_SCHEDULE_WEEK_START }
        : null;
    return okAck({ data: { rows, running } });
  } catch (e) {
    return failAck(e);
  }
}

/** Approve one pending ADP unavailability request (headless Cloud Run job). */
export async function approveUnavailabilityAction(
  rowKey: string,
): Promise<ActionAck<{ executionName: string }>> {
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
  try {
    return okAck({ data: await pollUnavailabilityApprove(opts) });
  } catch (e) {
    return failAck(e);
  }
}

/** Start "Sync ADP" — every ADP read in one Cloud Run login. */
export async function syncAdpAction(targetDate: string): Promise<ActionAck<AdpSyncStart>> {
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
  try {
    const data = await pollPunchFixApply({ store: DEFAULT_STORE, ...opts });
    return okAck({ data });
  } catch (e) {
    return failAck(e);
  }
}
