import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  revalidatePath: vi.fn(),
}));

const getAutomation = vi.fn();
const reviewBonusLeaderboardForPeriod = vi.fn();
const listPayPeriodsWithPaidStatus = vi.fn();
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
  insertAutomationPost: vi.fn(),
  upsertAutomation: vi.fn(),
}));

vi.mock("@/lib/automations/clickup", () => ({
  ensureDmChannel: vi.fn(),
  postChatMessage: vi.fn(),
  DEFAULT_WORKSPACE_ID: "ws",
}));

vi.mock("@/lib/automations/varyCopy", () => ({
  varyMotivationalCopy: (...a: unknown[]) => varyMotivationalCopy(...a),
}));

import { previewTeamPulseAction } from "@/app/automations/actions";

describe("previewTeamPulseAction period (Issue #245)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    operatorEmail.mockResolvedValue("tester@example.com");
    getAutomation.mockResolvedValue({
      template:
        "Sharing current pay cycle's leaderboard.\n\n{leaderboard}\n\nBye",
    });
    reviewBonusLeaderboardForPeriod.mockResolvedValue([
      {
        employee: "Example, Alex",
        total_bonus: 40,
        period_start: "2026-07-13",
        period_end: "2026-07-26",
      },
    ]);
    listPayPeriodsWithPaidStatus.mockResolvedValue([
      {
        period_start: "2026-07-13",
        period_end: "2026-07-26",
        unpaid: false,
        submitted: false,
        is_current: false,
      },
    ]);
    varyMotivationalCopy.mockImplementation(async (base: string) => ({
      text: base,
      varied: false,
    }));
  });

  it("uses the explicit period for the leaderboard", async () => {
    const ack = await previewTeamPulseAction("2026-07-13");
    expect(ack.ok).toBe(true);
    expect(reviewBonusLeaderboardForPeriod).toHaveBeenCalledWith("2026-07-13");
    if (ack.ok) {
      expect(ack.data?.content).toContain("Alex Example");
      expect(ack.data?.content).toContain("$40");
      expect(ack.data?.content).toMatch(/Jul 13/);
      expect(ack.data?.content).not.toMatch(/current pay cycle/i);
    }
  });

  it("rejects invalid period", async () => {
    const ack = await previewTeamPulseAction("bad");
    expect(ack.ok).toBe(false);
    if (!ack.ok) expect(ack.error).toMatch(/Invalid pay period/);
    expect(reviewBonusLeaderboardForPeriod).not.toHaveBeenCalled();
  });
});
