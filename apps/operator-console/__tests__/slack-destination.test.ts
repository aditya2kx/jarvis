import { describe, expect, it } from "vitest";
import { SLACK_CHAT } from "@/lib/config/stores";
import { parseSlackDestination, slackDestinationLabel } from "@/lib/automations/slackDestination";

const houston = SLACK_CHAT.houston!;

describe("Slack destination (Houston automations)", () => {
  it("falls back to the drafts channel for ClickUp or unknown values", () => {
    expect(parseSlackDestination("slack_channel")).toBe("slack_channel");
    expect(parseSlackDestination("drafts")).toBe("slack_drafts");
    expect(parseSlackDestination(undefined)).toBe("slack_drafts");
  });

  it("labels each destination with the store's channel", () => {
    expect(slackDestinationLabel("slack_drafts", houston, "coverage")).toBe("Draft in #palmetto-app-drafts");
    expect(slackDestinationLabel("slack_dm", houston, "coverage")).toBe("Draft to my Slack DM");
    expect(slackDestinationLabel("slack_channel", houston, "coverage")).toBe(
      "Post to #shifts-coverage-trade-houston-heights",
    );
    expect(slackDestinationLabel("slack_channel", houston, "running")).toBe(
      "Post to #running-houston-heights-palmetto",
    );
  });

  it("Austin stays on ClickUp", () => {
    expect(SLACK_CHAT.palmetto).toBeUndefined();
  });
});
