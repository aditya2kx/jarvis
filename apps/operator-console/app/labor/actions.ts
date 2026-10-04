"use server";

import { failAck, okAck, type ActionAck } from "@/lib/actions/types";
import { FEATURES } from "@/lib/config/features";
import { DEFAULT_STORE, operatorEmail } from "@/lib/auth/identity";
import { punchGapFor } from "@/lib/bq/queries";
import { recordPunchGapDecision } from "@/lib/bq/writes";
import {
  buildPunchGaps,
  formatClock,
  validateDecision,
  type DecisionInput,
} from "@/lib/labor/punch-gaps";
import {
  pollAdpScheduleSync,
  startAdpScheduleSync,
  type ScheduleSyncPoll,
  type ScheduleSyncStart,
} from "@/lib/bhaga/schedule-sync";
import {
  pollAdpTimecardSync,
  startAdpTimecardSync,
  type HoursSyncPoll,
  type HoursSyncStart,
} from "@/lib/bhaga/hours-sync";
import {
  pollPunchFixApply,
  startPunchFixApply,
  type PunchFixPoll,
  type PunchFixStart,
} from "@/lib/bhaga/punch-fix";

/** Start schedule sync (local scrape when BYPASS_IAP, else Cloud Run). */
export async function syncScheduledShiftsAction(): Promise<
  ActionAck<ScheduleSyncStart>
> {
  try {
    const data = await startAdpScheduleSync(DEFAULT_STORE);
    return okAck({
      data,
      queued: ["adp-scheduled-shifts"],
      message: data.message,
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Poll BQ scraped_at (+ Cloud Run execution when cloud mode). */
export async function pollScheduledShiftsSyncAction(opts: {
  baselineScrapedAt: string | null;
  executionName?: string | null;
}): Promise<ActionAck<ScheduleSyncPoll>> {
  try {
    const data = await pollAdpScheduleSync(opts);
    return okAck({ data });
  } catch (e) {
    return failAck(e);
  }
}

/** Start Timecard scrape (local when BYPASS_IAP, else Cloud Run). Skips pay_info. */
export async function syncClockedHoursAction(
  targetDate: string,
): Promise<ActionAck<HoursSyncStart>> {
  try {
    const data = await startAdpTimecardSync(DEFAULT_STORE, targetDate);
    return okAck({
      data,
      queued: ["adp-clocked-hours"],
      message: data.message,
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Poll BQ adp_shifts scraped_at (+ Cloud Run execution when cloud mode). */
export async function pollClockedHoursSyncAction(opts: {
  baselineScrapedAt: string | null;
  executionName?: string | null;
}): Promise<ActionAck<HoursSyncPoll>> {
  try {
    const data = await pollAdpTimecardSync(opts);
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
