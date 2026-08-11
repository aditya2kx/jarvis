import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  revalidatePath: vi.fn(),
}));

const getAutomation = vi.fn();
const reviewBonusLeaderboardForPeriod = vi.fn();
const listPayPeriodsWithPaidStatus = vi.fn();
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
  reviewBonusLeaderboardForPeriod: (...a: unknown[]) =>
    reviewBonusLeaderboardForPeriod(...a),
  listPayPeriodsWithPaidStatus: (...a: unknown[]) =>
    listPayPeriodsWithPaidStatus(...a),
}));

vi.mock("@/lib/bq/writes", () => ({
  hasAutomationPostToday: vi.fn(),
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

const PERIOD = "2026-07-27";

describe("postTeamPulseOnceAction manual (Issue #245)", () => {
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
    reviewBonusLeaderboardForPeriod.mockResolvedValue([
      {
        employee: "Example, Alex",
        total_bonus: 40,
        period_start: PERIOD,
        period_end: "2026-08-08",
      },
    ]);
    listPayPeriodsWithPaidStatus.mockResolvedValue([
      {
        period_start: PERIOD,
        period_end: "2026-08-08",
        unpaid: true,
        is_current: false,
      },
    ]);
    varyMotivationalCopy.mockResolvedValue({
      text: "Hi\n\n*   **Alex Example** leading with $40.\n\nBye",
      varied: false,
    });
    ensureDmChannel.mockResolvedValue({ id: "dm-1" });
    postChatMessage.mockResolvedValue({ id: "msg-1" });
  });

  it("loads leaderboard for the selected period and posts", async () => {
    const ack = await postTeamPulseOnceAction(PERIOD);
    expect(ack.ok).toBe(true);
    expect(reviewBonusLeaderboardForPeriod).toHaveBeenCalledWith(PERIOD);
    expect(postChatMessage).toHaveBeenCalledTimes(1);
    expect(insertAutomationPost).toHaveBeenCalledTimes(1);
  });

  it("allows multiple manual posts the same CT day", async () => {
    postChatMessage
      .mockResolvedValueOnce({ id: "msg-1" })
      .mockResolvedValueOnce({ id: "msg-2" });
    const first = await postTeamPulseOnceAction(PERIOD);
    const second = await postTeamPulseOnceAction(PERIOD);
    expect(first.ok).toBe(true);
    expect(second.ok).toBe(true);
    expect(postChatMessage).toHaveBeenCalledTimes(2);
    expect(insertAutomationPost).toHaveBeenCalledTimes(2);
  });

  it("rejects invalid period", async () => {
    const ack = await postTeamPulseOnceAction("not-a-date");
    expect(ack.ok).toBe(false);
    if (!ack.ok) expect(ack.error).toMatch(/Invalid pay period/);
    expect(reviewBonusLeaderboardForPeriod).not.toHaveBeenCalled();
  });
});
