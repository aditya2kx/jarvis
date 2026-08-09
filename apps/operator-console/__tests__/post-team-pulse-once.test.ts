import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  revalidatePath: vi.fn(),
}));

const hasAutomationPostToday = vi.fn();
const getAutomation = vi.fn();
const openReviewBonusLeaderboard = vi.fn();
const upsertAutomation = vi.fn();
const insertAutomationPost = vi.fn();
const postChatMessage = vi.fn();
const ensureDmChannel = vi.fn();
const varyMotivationalCopy = vi.fn();
const operatorEmail = vi.fn();

vi.mock("@/lib/auth/identity", () => ({
  operatorEmail: (...a: unknown[]) => operatorEmail(...a),
  DEFAULT_STORE: "palmetto",
}));

vi.mock("@/lib/bq/queries", () => ({
  getAutomation: (...a: unknown[]) => getAutomation(...a),
  openReviewBonusLeaderboard: (...a: unknown[]) => openReviewBonusLeaderboard(...a),
}));

vi.mock("@/lib/bq/writes", () => ({
  hasAutomationPostToday: (...a: unknown[]) => hasAutomationPostToday(...a),
  insertAutomationPost: (...a: unknown[]) => insertAutomationPost(...a),
  upsertAutomation: (...a: unknown[]) => upsertAutomation(...a),
}));

vi.mock("@/lib/automations/clickup", () => ({
  ensureDmChannel: (...a: unknown[]) => ensureDmChannel(...a),
  postChatMessage: (...a: unknown[]) => postChatMessage(...a),
  DEFAULT_WORKSPACE_ID: "ws",
}));

vi.mock("@/lib/automations/varyCopy", () => ({
  varyMotivationalCopy: (...a: unknown[]) => varyMotivationalCopy(...a),
}));

import { postTeamPulseOnceAction } from "@/app/automations/actions";

describe("postTeamPulseOnceAction once-gate (Issue #233)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    operatorEmail.mockResolvedValue("tester@example.com");
    getAutomation.mockResolvedValue({
      enabled: true,
      template: "Hi\n\n{leaderboard}\n\nBye",
      destination: "dm",
      channel_id: "ch",
      dm_user_id: "198109189",
    });
    openReviewBonusLeaderboard.mockResolvedValue([
      { employee: "Example, Alex", total_bonus: 40, period_start: "2026-07-27", period_end: "2026-08-08" },
    ]);
    varyMotivationalCopy.mockResolvedValue({
      text: "Hi\n\n*   **Alex Example** leading with $40.\n\nBye",
      varied: false,
    });
    ensureDmChannel.mockResolvedValue({ id: "dm-1" });
    postChatMessage.mockResolvedValue({ id: "msg-1" });
  });

  it("fails on first once-check without calling ClickUp", async () => {
    hasAutomationPostToday.mockResolvedValue(true);
    const ack = await postTeamPulseOnceAction();
    expect(ack.ok).toBe(false);
    if (!ack.ok) expect(ack.error).toMatch(/Already posted today/);
    expect(postChatMessage).not.toHaveBeenCalled();
    expect(insertAutomationPost).not.toHaveBeenCalled();
  });

  it("pre-ClickUp recheck blocks when a race posts between compose and send", async () => {
    hasAutomationPostToday
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    const ack = await postTeamPulseOnceAction();
    expect(ack.ok).toBe(false);
    if (!ack.ok) expect(ack.error).toMatch(/Already posted today/);
    expect(hasAutomationPostToday).toHaveBeenCalledTimes(2);
    expect(postChatMessage).not.toHaveBeenCalled();
    expect(insertAutomationPost).not.toHaveBeenCalled();
  });
});
