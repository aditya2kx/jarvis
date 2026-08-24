import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const getAccessToken = vi.fn();
vi.mock("google-auth-library", () => ({
  GoogleAuth: class {
    getClient = async () => ({ getAccessToken });
  },
}));

import {
  pickRecomputeAnchorDate,
  triggerModelRecompute,
  triggerOrderRecoRefresh,
  triggerPayrollDraft,
} from "@/lib/bhaga/recompute";

describe("pickRecomputeAnchorDate", () => {
  it("returns null for empty input", () => {
    expect(pickRecomputeAnchorDate([])).toBeNull();
    expect(pickRecomputeAnchorDate(["", ""])).toBeNull();
  });

  it("dedupes and picks the latest date as the single job anchor", () => {
    expect(
      pickRecomputeAnchorDate(["2026-07-06", "2026-07-09", "2026-07-06", "2026-07-08"]),
    ).toBe("2026-07-09");
  });

  it("handles a single date", () => {
    expect(pickRecomputeAnchorDate(["2026-07-06"])).toBe("2026-07-06");
  });
});

describe("triggerModelRecompute", () => {
  beforeEach(() => {
    getAccessToken.mockReset();
    getAccessToken.mockResolvedValue({ token: "test-token" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: async () => "",
        json: async () => ({}),
      }),
    );
  });

  it("POSTs Cloud Run :run exactly once for a multi-date batch", async () => {
    const touched = await triggerModelRecompute([
      "2026-07-06",
      "2026-07-09",
      "2026-07-06",
      "2026-07-08",
    ]);
    expect(touched).toEqual(["2026-07-06", "2026-07-08", "2026-07-09"]);
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(String(url)).toMatch(/:run$/);
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body));
    const env = body.overrides.containerOverrides[0].env as { name: string; value: string }[];
    expect(env.find((e) => e.name === "REFRESH_DATE")?.value).toBe("2026-07-09");
    expect(env.find((e) => e.name === "BHAGA_FORCE_MODEL_RECOMPUTE")?.value).toBe("1");
  });

  it("returns [] without calling Cloud Run when dates are empty", async () => {
    expect(await triggerModelRecompute([])).toEqual([]);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("triggerOrderRecoRefresh", () => {
  beforeEach(() => {
    getAccessToken.mockReset();
    getAccessToken.mockResolvedValue({ token: "test-token" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: async () => "",
        json: async () => ({}),
      }),
    );
  });

  it("POSTs :run with BHAGA_ORDER_RECO_ONLY", async () => {
    await triggerOrderRecoRefresh("palmetto");
    expect(fetch).toHaveBeenCalledTimes(1);
    const [, init] = vi.mocked(fetch).mock.calls[0]!;
    const body = JSON.parse(String(init?.body));
    const env = body.overrides.containerOverrides[0].env as { name: string; value: string }[];
    expect(env.find((e) => e.name === "BHAGA_ORDER_RECO_ONLY")?.value).toBe("1");
    expect(env.find((e) => e.name === "BHAGA_STORE")?.value).toBe("palmetto");
  });
});

describe("triggerPayrollDraft", () => {
  beforeEach(() => {
    getAccessToken.mockReset();
    getAccessToken.mockResolvedValue({ token: "test-token" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: async () => "",
        json: async () => ({
          metadata: {
            name: "projects/jarvis-bhaga-prod/locations/us-central1/jobs/bhaga-daily-refresh/executions/x",
          },
        }),
      }),
    );
  });

  it("POSTs :run with draft-only env and period bounds", async () => {
    const out = await triggerPayrollDraft("palmetto", "2026-08-10", "2026-08-23");
    expect(out.executionName).toContain("/executions/");
    expect(fetch).toHaveBeenCalledTimes(1);
    const [, init] = vi.mocked(fetch).mock.calls[0]!;
    const body = JSON.parse(String(init?.body));
    const env = body.overrides.containerOverrides[0].env as { name: string; value: string }[];
    expect(env.find((e) => e.name === "BHAGA_PAYROLL_DRAFT_ONLY")?.value).toBe("1");
    expect(env.find((e) => e.name === "BHAGA_ADP_PAYROLL_DRAFT")?.value).toBe("1");
    expect(env.find((e) => e.name === "BHAGA_PAYROLL_PERIOD_START")?.value).toBe(
      "2026-08-10",
    );
    expect(env.find((e) => e.name === "BHAGA_PAYROLL_PERIOD_END")?.value).toBe(
      "2026-08-23",
    );
    expect(env.find((e) => e.name === "BHAGA_SKIP_ADP")).toBeUndefined();
  });

  it("rejects non-ISO dates without calling Cloud Run", async () => {
    await expect(
      triggerPayrollDraft("palmetto", "08/10/2026", "2026-08-23"),
    ).rejects.toThrow(/YYYY-MM-DD/);
    expect(fetch).not.toHaveBeenCalled();
  });
});
