"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import { asAck, type ActionAck } from "@/lib/actions/types";
import { adpDirectoryRoster, adpUnavailability, getAutomation } from "@/lib/bq/queries";
import { insertAutomationPost, upsertAutomation } from "@/lib/bq/writes";
import { triggerUnavailabilityReminder } from "@/lib/bhaga/recompute";
import { ensureDmChannel, postChatMessage, DEFAULT_WORKSPACE_ID } from "@/lib/automations/clickup";
import { chicagoTodayIso, displayName, parseDays } from "@/lib/automations/teamPulse";
import {
  checkWeeks,
  clampLeadWeeks,
  composeReminder,
  reminderKind,
  REMINDER_AUTOMATION_ID,
  REMINDER_DEFAULT_DAYS,
  REMINDER_DEFAULT_FOLLOWUP,
  REMINDER_DEFAULT_TEMPLATE,
  REMINDER_DRAFTS_CHANNEL_ID,
  REMINDER_MAX_LEAD_WEEKS,
  REMINDER_MIN_LEAD_WEEKS,
  unmarkedPeople,
} from "@/lib/automations/unavailabilityReminder";
import { actionDenied } from "@/lib/access/server";

const DM_USER_ID = "198109189";
const PATH = "/automations/unavailability-reminder";

export type ReminderConfigInput = {
  enabled: boolean;
  days_of_week: number[];
  hour_local: number;
  minute_local: number;
  lead_weeks: number;
  template: string;
  followup_template: string;
};

function check(input: ReminderConfigInput): void {
  if (!input.days_of_week.length) throw new Error("Pick at least one day.");
  if (!Number.isInteger(input.hour_local) || input.hour_local < 0 || input.hour_local > 23) {
    throw new Error("Hour must be 0–23.");
  }
  if (!Number.isInteger(input.minute_local) || input.minute_local < 0 || input.minute_local > 59) {
    throw new Error("Minute must be 0–59.");
  }
  if (
    !Number.isInteger(input.lead_weeks) ||
    input.lead_weeks < REMINDER_MIN_LEAD_WEEKS ||
    input.lead_weeks > REMINDER_MAX_LEAD_WEEKS
  ) {
    throw new Error(`Weeks ahead must be ${REMINDER_MIN_LEAD_WEEKS}–${REMINDER_MAX_LEAD_WEEKS}.`);
  }
  if (!input.template.trim() || !input.followup_template.trim()) {
    throw new Error("Both messages need text.");
  }
}

export async function saveReminderConfigAction(input: ReminderConfigInput): Promise<ActionAck> {
  const denied = await actionDenied("saveReminderConfigAction");
  if (denied) return denied;
  return asAck(async () => {
    check(input);
    const prev = await getAutomation(DEFAULT_STORE, REMINDER_AUTOMATION_ID);
    const drafts = !prev || prev.destination === "drafts";
    await upsertAutomation(
      DEFAULT_STORE,
      REMINDER_AUTOMATION_ID,
      {
        enabled: input.enabled,
        days_of_week: [...input.days_of_week].sort(),
        hour_local: input.hour_local,
        minute_local: input.minute_local,
        timezone: "America/Chicago",
        destination: drafts ? "drafts" : "dm",
        channel_id: drafts ? prev?.channel_id || REMINDER_DRAFTS_CHANNEL_ID : "",
        dm_user_id: prev?.dm_user_id || DM_USER_ID,
        workspace_id: DEFAULT_WORKSPACE_ID,
        template: input.template,
        followup_template: input.followup_template,
        lead_weeks: input.lead_weeks,
      },
      await operatorEmail(),
    );
    revalidatePath("/automations");
    revalidatePath(PATH);
  }, "Reminder settings saved.");
}

/** Roster people with a blank week in this Friday's publish horizon, as `@First Last`. */
async function flaggedNames(todayIso: string, leadWeeks: number): Promise<string[]> {
  const [roster, rows] = await Promise.all([adpDirectoryRoster(DEFAULT_STORE), adpUnavailability(DEFAULT_STORE)]);
  const active = roster.filter((r) => r.employment_status === "Active").map((r) => r.employee);
  return unmarkedPeople(active, rows, checkWeeks(todayIso, leadWeeks)).map((n) => `@${displayName(n)}`);
}

/** Compose for a CT date (default today) from the saved config, or `draft` when given. */
async function composeFor(dateIso: string | undefined, draft?: ReminderConfigInput) {
  const cfg = await getAutomation(DEFAULT_STORE, REMINDER_AUTOMATION_ID);
  const today = dateIso && /^\d{4}-\d{2}-\d{2}$/.test(dateIso) ? dateIso : chicagoTodayIso();
  const days = draft?.days_of_week ?? (cfg ? parseDays(cfg.days_of_week) : REMINDER_DEFAULT_DAYS);
  const lead = clampLeadWeeks(draft?.lead_weeks ?? cfg?.lead_weeks);
  const template = draft?.template ?? (cfg?.template || REMINDER_DEFAULT_TEMPLATE);
  const followup = draft?.followup_template ?? (cfg?.followup_template || REMINDER_DEFAULT_FOLLOWUP);
  const text = reminderKind(today, days) === "reminder" ? template : followup;
  const flagged = text.includes("{flagged}") ? await flaggedNames(today, lead) : [];
  return { today, cfg, flagged, ...composeReminder(today, days, template, followup, lead, flagged) };
}

export async function previewReminderAction(
  dateIso: string,
  draft: ReminderConfigInput,
): Promise<ActionAck<{ kind: string; content: string; flagged: string[] }>> {
  const denied = await actionDenied("previewReminderAction");
  if (denied) return denied;
  return asAck(async () => {
    const { kind, content, flagged } = await composeFor(dateIso, draft);
    return { kind, content, flagged };
  }, "Preview ready.");
}

/**
 * Send today's saved message now; it counts as today's send, so the scheduled tick then skips
 * today. Drafts delivery starts the Cloud Run job, which types it into the drafts channel.
 */
export async function sendReminderOnceAction(): Promise<ActionAck<{ message_id: string }>> {
  const denied = await actionDenied("sendReminderOnceAction");
  if (denied) return denied;
  return asAck(async () => {
    const by = await operatorEmail();
    const { today, cfg, content } = await composeFor(undefined);
    if (cfg?.destination === "drafts") {
      const { executionName } = await triggerUnavailabilityReminder(DEFAULT_STORE);
      revalidatePath(PATH);
      return { message_id: executionName };
    }
    const dm = await ensureDmChannel([cfg?.dm_user_id || DM_USER_ID], DEFAULT_WORKSPACE_ID);
    const created = await postChatMessage(dm.id, content, DEFAULT_WORKSPACE_ID);
    await insertAutomationPost({
      store: DEFAULT_STORE,
      automation_id: REMINDER_AUTOMATION_ID,
      post_date_ct: today,
      destination: "dm",
      channel_id: dm.id,
      message_id: created.id,
      content,
      dry_run: false,
      trigger: "once",
      updated_by: by,
    });
    revalidatePath(PATH);
    return { message_id: created.id };
  }, "Sent.");
}
