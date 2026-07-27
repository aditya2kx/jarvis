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

describe("forecast forward vs Period accuracy (Issue #202)", () => {
  beforeEach(() => {
    vi.resetModules();
    q.mockReset();
    dateParam.mockClear();
    q.mockResolvedValue([]);
  });

  it("forecastForwardByGrain ignores Period — no @start/@end, filters >= Chicago today", async () => {
    const { forecastForwardByGrain } = await import("@/lib/bq/queries");
    await forecastForwardByGrain("day");

    expect(q).toHaveBeenCalledTimes(1);
    const [sql, params] = q.mock.calls[0] as [string, Record<string, unknown> | undefined];
    expect(sql).toContain("vw_model_forecast");
    expect(sql).toContain("CURRENT_DATE('America/Chicago')");
    expect(sql).not.toMatch(/BETWEEN @start AND @end/);
    expect(params).toBeUndefined();
    expect(dateParam).not.toHaveBeenCalled();
  });

  it("forecastByGrain still Period-clips with @start/@end", async () => {
    const { forecastByGrain } = await import("@/lib/bq/queries");
    await forecastByGrain(
      { start: "2026-07-01", end: "2026-07-31", label: "This month", preset: "this_month" },
      "day",
    );

    expect(q).toHaveBeenCalledTimes(1);
    const [sql, params] = q.mock.calls[0] as [string, Record<string, unknown>];
    expect(sql).toMatch(/BETWEEN @start AND @end/);
    expect(params).toEqual({
      start: { __date: "2026-07-01" },
      end: { __date: "2026-07-31" },
    });
  });

  it("forecastAccuracyByGrain remains Period-scoped", async () => {
    const { forecastAccuracyByGrain } = await import("@/lib/bq/queries");
    await forecastAccuracyByGrain(
      { start: "2026-06-01", end: "2026-06-30", label: "Last month", preset: "last_month" },
      "week",
    );

    expect(q).toHaveBeenCalledTimes(1);
    const [sql, params] = q.mock.calls[0] as [string, Record<string, unknown>];
    expect(sql).toContain("vw_forecast_accuracy");
    expect(sql).toMatch(/BETWEEN @start AND @end/);
    expect(params).toEqual({
      start: { __date: "2026-06-01" },
      end: { __date: "2026-06-30" },
    });
  });
});
