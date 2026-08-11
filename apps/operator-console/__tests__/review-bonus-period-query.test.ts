import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const q = vi.fn();
const dateParam = vi.fn((d: string) => ({ __date: d }));
const intParam = vi.fn((n: number) => n);
const fq = vi.fn((name: string) => `\`${name}\``);

vi.mock("@/lib/bq/client", () => ({
  q: (...args: unknown[]) => q(...args),
  dateParam: (d: string) => dateParam(d),
  intParam: (n: number) => intParam(n),
  fq: (name: string) => fq(name),
  mutate: vi.fn(),
}));

describe("reviewBonus*ForPeriod (Issue #245)", () => {
  beforeEach(() => {
    vi.resetModules();
    q.mockReset();
    dateParam.mockClear();
    q.mockResolvedValue([]);
  });

  it("reviewBonusLeaderboardForPeriod filters by @start without is_open", async () => {
    const { reviewBonusLeaderboardForPeriod } = await import("@/lib/bq/queries");
    await reviewBonusLeaderboardForPeriod("2026-07-13");

    expect(q).toHaveBeenCalledTimes(1);
    const [sql, params] = q.mock.calls[0] as [string, Record<string, unknown>];
    expect(sql).toContain("model_review_bonus_period");
    expect(sql).toContain("m.period_start = @start");
    expect(sql).not.toMatch(/is_open\s*=\s*TRUE/);
    expect(params).toEqual({ start: { __date: "2026-07-13" } });
    expect(dateParam).toHaveBeenCalledWith("2026-07-13");
  });

  it("reviewBonusMetaForPeriod filters by @start without is_open", async () => {
    const { reviewBonusMetaForPeriod } = await import("@/lib/bq/queries");
    await reviewBonusMetaForPeriod("2026-07-13");

    expect(q).toHaveBeenCalledTimes(1);
    const [sql, params] = q.mock.calls[0] as [string, Record<string, unknown>];
    expect(sql).toContain("model_review_bonus_period");
    expect(sql).toContain("m.period_start = @start");
    expect(sql).not.toMatch(/is_open\s*=\s*TRUE/);
    expect(params).toEqual({ start: { __date: "2026-07-13" } });
  });

  it("openReviewBonusLeaderboard still requires is_open", async () => {
    const { openReviewBonusLeaderboard } = await import("@/lib/bq/queries");
    await openReviewBonusLeaderboard();

    expect(q).toHaveBeenCalledTimes(1);
    const [sql] = q.mock.calls[0] as [string];
    expect(sql).toMatch(/is_open\s*=\s*TRUE/);
  });
});
