"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import { asAck, type ActionAck } from "@/lib/actions/types";
import { getAutomation } from "@/lib/bq/queries";
import { insertAutomationPost, upsertAutomation } from "@/lib/bq/writes";
import { ensureDmChannel, postChatMessage, DEFAULT_WORKSPACE_ID } from "@/lib/automations/clickup";
import { chicagoTodayIso, parseDays } from "@/lib/automations/teamPulse";
import {
  composeReminder,
  REMINDER_AUTOMATION_ID,
  REMINDER_DEFAULT_DAYS,
  REMINDER_DEFAULT_FOLLOWUP,
  REMINDER_DEFAULT_TEMPLATE,
} from "@/lib/automations/unavailabilityReminder";

const DM_USER_ID = "198109189";
const PATH = "/automations/unavailability-reminder";

export type ReminderConfigInput = {
  enabled: boolean;
  days_of_week: number[];
  hour_local: number;
  minute_local: number;
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
  if (!input.template.trim() || !input.followup_template.trim()) {
    throw new Error("Both messages need text.");
  }
}

export async function saveReminderConfigAction(input: ReminderConfigInput): Promise<ActionAck> {
  return asAck(async () => {
    check(input);
    const prev = await getAutomation(DEFAULT_STORE, REMINDER_AUTOMATION_ID);
    await upsertAutomation(
      DEFAULT_STORE,
      REMINDER_AUTOMATION_ID,
      {
        enabled: input.enabled,
        days_of_week: [...input.days_of_week].sort(),
        hour_local: input.hour_local,
        minute_local: input.minute_local,
        timezone: "America/Chicago",
        destination: "dm",
        channel_id: "",
        dm_user_id: prev?.dm_user_id || DM_USER_ID,
        workspace_id: DEFAULT_WORKSPACE_ID,
        template: input.template,
        followup_template: input.followup_template,
      },
      await operatorEmail(),
    );
    revalidatePath("/automations");
    revalidatePath(PATH);
  }, "Reminder settings saved.");
}

/** Compose for a CT date (default today) from the saved config, or `draft` when given. */
async function composeFor(dateIso: string | undefined, draft?: ReminderConfigInput) {
  const cfg = await getAutomation(DEFAULT_STORE, REMINDER_AUTOMATION_ID);
  const today = dateIso && /^\d{4}-\d{2}-\d{2}$/.test(dateIso) ? dateIso : chicagoTodayIso();
  const days = draft?.days_of_week ?? (cfg ? parseDays(cfg.days_of_week) : REMINDER_DEFAULT_DAYS);
  return {
    today,
    ...composeReminder(
      today,
      days,
      draft?.template ?? (cfg?.template || REMINDER_DEFAULT_TEMPLATE),
      draft?.followup_template ?? (cfg?.followup_template || REMINDER_DEFAULT_FOLLOWUP),
    ),
  };
}

export async function previewReminderAction(
  dateIso: string,
  draft: ReminderConfigInput,
): Promise<ActionAck<{ kind: string; content: string }>> {
  return asAck(async () => {
    const { kind, content } = await composeFor(dateIso, draft);
    return { kind, content };
  }, "Preview ready.");
}

/** DM the operator today's message now. Logged as today's send, so the scheduled tick then skips today. */
export async function sendReminderOnceAction(): Promise<ActionAck<{ message_id: string }>> {
  return asAck(async () => {
    const by = await operatorEmail();
    const cfg = await getAutomation(DEFAULT_STORE, REMINDER_AUTOMATION_ID);
    const { today, content } = await composeFor(undefined);
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
  }, "Sent to your ClickUp DM.");
}
