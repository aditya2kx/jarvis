"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import { asAck, type ActionAck } from "@/lib/actions/types";
import {
  getAutomation,
  listPayPeriodsWithPaidStatus,
  reviewBonusLeaderboardForPeriod,
  type ReviewBonusLeaderboardRow,
} from "@/lib/bq/queries";
import {
  hasAutomationPostToday,
  insertAutomationPost,
  upsertAutomation,
  type AutomationUpsert,
} from "@/lib/bq/writes";
import { ensureDmChannel, postChatMessage, DEFAULT_WORKSPACE_ID } from "@/lib/automations/clickup";
import {
  AUTOMATION_ID,
  chicagoTodayIso,
  composeMessage,
  DEFAULT_TEMPLATE,
  formatLeaderboard,
  type PayCycleContext,
} from "@/lib/automations/teamPulse";
import { varyMotivationalCopy } from "@/lib/automations/varyCopy";

async function payCycleForPeriod(
  periodStart: string,
  rows: ReviewBonusLeaderboardRow[],
): Promise<PayCycleContext> {
  const periods = await listPayPeriodsWithPaidStatus(6);
  const opt = periods.find((p) => p.period_start === periodStart);
  return {
    periodStart,
    periodEnd: opt?.period_end ?? rows[0]?.period_end ?? null,
    isCurrent: Boolean(opt?.is_current),
  };
}

export type TeamPulseConfigInput = {
  enabled: boolean;
  days_of_week: number[];
  hour_local: number;
  minute_local: number;
  timezone: string;
  destination: "dm" | "channel";
  channel_id: string;
  dm_user_id: string;
  /** Ignored — always Palmetto workspace. Kept optional for back-compat. */
  workspace_id?: string;
  template: string;
};

const SEED = {
  enabled: true,
  days_of_week: [1, 3, 6],
  hour_local: 8,
  minute_local: 0,
  timezone: "America/Chicago",
  destination: "dm" as const,
  channel_id: "8cr6661-737",
  dm_user_id: "198109189",
  workspace_id: DEFAULT_WORKSPACE_ID,
  template: DEFAULT_TEMPLATE,
};

export async function saveTeamPulseConfigAction(
  input: TeamPulseConfigInput,
): Promise<ActionAck> {
  return asAck(async () => {
    const by = await operatorEmail();
    const cfg: AutomationUpsert = {
      enabled: input.enabled,
      days_of_week: input.days_of_week,
      hour_local: input.hour_local,
      minute_local: input.minute_local,
      timezone: input.timezone || "America/Chicago",
      destination: input.destination,
      channel_id: input.channel_id,
      dm_user_id: input.dm_user_id,
      workspace_id: DEFAULT_WORKSPACE_ID,
      template: input.template,
    };
    await upsertAutomation(DEFAULT_STORE, AUTOMATION_ID, cfg, by);
    revalidatePath("/automations");
    revalidatePath("/automations/team-pulse");
  }, "Team pulse settings saved.");
}

function assertPeriodStart(periodStart: string): void {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(periodStart)) {
    throw new Error("Invalid pay period.");
  }
}

export async function previewTeamPulseAction(
  periodStart: string,
): Promise<ActionAck<{ content: string; varied: boolean }>> {
  return asAck(async () => {
    assertPeriodStart(periodStart);
    const cfg = await getAutomation(DEFAULT_STORE, AUTOMATION_ID);
    const template = cfg?.template || DEFAULT_TEMPLATE;
    const rows = await reviewBonusLeaderboardForPeriod(periodStart);
    const leaderboard = formatLeaderboard(rows);
    const cycle = await payCycleForPeriod(periodStart, rows);
    const base = composeMessage(template, leaderboard, cycle);
    const { text, varied } = await varyMotivationalCopy(base, leaderboard);
    return { content: text, varied };
  }, "Preview ready.");
}

export async function postTeamPulseOnceAction(
  periodStart: string,
): Promise<
  ActionAck<{ message_id: string; destination: string; channel_id: string }>
> {
  return asAck(async () => {
    assertPeriodStart(periodStart);
    const by = await operatorEmail();
    let cfg = await getAutomation(DEFAULT_STORE, AUTOMATION_ID);
    if (!cfg) {
      await upsertAutomation(DEFAULT_STORE, AUTOMATION_ID, SEED, by);
      cfg = await getAutomation(DEFAULT_STORE, AUTOMATION_ID);
    }
    if (!cfg) throw new Error("Failed to load team-pulse config after seed");

    const postDate = chicagoTodayIso();
    if (await hasAutomationPostToday(DEFAULT_STORE, AUTOMATION_ID, postDate)) {
      throw new Error(
        `Already posted today (${postDate} CT). Wait until tomorrow or use a different date.`,
      );
    }

    const rows = await reviewBonusLeaderboardForPeriod(periodStart);
    const leaderboard = formatLeaderboard(rows);
    const cycle = await payCycleForPeriod(periodStart, rows);
    const base = composeMessage(
      cfg.template || DEFAULT_TEMPLATE,
      leaderboard,
      cycle,
    );
    const { text: content } = await varyMotivationalCopy(base, leaderboard);
    const workspace = DEFAULT_WORKSPACE_ID;
    const dest =
      (cfg.destination || "dm").toLowerCase() === "channel" ? "channel" : "dm";

    let channelId = cfg.channel_id || "8cr6661-737";
    if (dest === "dm") {
      const dm = await ensureDmChannel(
        [cfg.dm_user_id || "198109189"],
        workspace,
      );
      channelId = dm.id;
    }

    // Re-check immediately before ClickUp write (narrows TOCTOU after compose/vary).
    if (await hasAutomationPostToday(DEFAULT_STORE, AUTOMATION_ID, postDate)) {
      throw new Error(
        `Already posted today (${postDate} CT). Wait until tomorrow or use a different date.`,
      );
    }

    const created = await postChatMessage(channelId, content, workspace);
    await insertAutomationPost({
      store: DEFAULT_STORE,
      automation_id: AUTOMATION_ID,
      post_date_ct: postDate,
      destination: dest,
      channel_id: channelId,
      message_id: created.id,
      content,
      dry_run: false,
      trigger: "once",
      updated_by: by,
    });
    revalidatePath("/automations/team-pulse");
    return {
      message_id: created.id,
      destination: dest,
      channel_id: channelId,
    };
  }, "Posted once.");
}
