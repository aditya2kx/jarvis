"use server";

import { failAck, okAck, type ActionAck } from "@/lib/actions/types";
import { DEFAULT_STORE } from "@/lib/auth/identity";
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
