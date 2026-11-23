import type { SLACK_CHAT } from "@/lib/config/stores";

/** Where a Slack store's automation posts — the values agents/bhaga/scripts/team_pulse.py routes on. */
export const SLACK_DESTINATIONS = ["slack_drafts", "slack_dm", "slack_channel"] as const;
export type SlackDestination = (typeof SLACK_DESTINATIONS)[number];
export type SlackChat = NonNullable<(typeof SLACK_CHAT)[keyof typeof SLACK_CHAT]>;

export function parseSlackDestination(raw: unknown): SlackDestination {
  return (SLACK_DESTINATIONS as readonly string[]).includes(String(raw))
    ? (raw as SlackDestination)
    : "slack_drafts";
}

/** Human label for the destination picker and the Automations card. */
export function slackDestinationLabel(dest: SlackDestination, chat: SlackChat, crew: "running" | "coverage"): string {
  switch (dest) {
    case "slack_drafts":
      return `Draft in #${chat.draftsChannel.name}`;
    case "slack_dm":
      return "Draft to my Slack DM";
    case "slack_channel":
      return `Post to #${(crew === "running" ? chat.runningChannel : chat.coverageChannel).name}`;
    default: {
      const never: never = dest;
      return never;
    }
  }
}
