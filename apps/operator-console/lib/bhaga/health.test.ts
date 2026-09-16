import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const getAccessToken = vi.fn().mockResolvedValue({ token: "tok" });
vi.mock("google-auth-library", () => ({
  GoogleAuth: class {
    getClient() {
      return Promise.resolve({ getAccessToken });
    }
  },
}));

const q = vi.fn();
vi.mock("@/lib/bq/client", () => ({
  q: (...args: unknown[]) => q(...args),
  fq: (name: string) => `\`p.d.${name}\``,
}));

vi.mock("server-only", () => ({}));

import {
  readPipelineHalt,
  getSystemHealth,
  ageInDays,
} from "@/lib/bhaga/health";

function fsResponse(fields: Record<string, unknown> | null, status = 200) {
  return {
    ok: status === 200,
    status,
    json: async () => (fields ? { fields } : {}),
    text: async () => "err",
  } as unknown as Response;
}

const futureIso = () => new Date(Date.now() + 3_600_000).toISOString();
const pastIso = () => new Date(Date.now() - 3_600_000).toISOString();

describe("readPipelineHalt", () => {
  beforeEach(() => {
    q.mockReset();
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => vi.unstubAllGlobals());

  it("reports not-halted when the doc does not exist", async () => {
    vi.mocked(fetch).mockResolvedValue(fsResponse(null, 404));
    expect(await readPipelineHalt()).toMatchObject({ halted: false });
  });

  it("reads reason and scope from a live halt", async () => {
    vi.mocked(fetch).mockResolvedValue(
      fsResponse({
        halted: { booleanValue: true },
        reason: { stringValue: "tip pool NOT conserved" },
        scope: { stringValue: "model" },
        since: { stringValue: "2026-09-07T21:40:00-05:00" },
        expires_at: { stringValue: futureIso() },
      }),
    );
    expect(await readPipelineHalt()).toEqual({
      halted: true,
      haltReason: "tip pool NOT conserved",
      haltScope: "model",
      haltSince: "2026-09-07T21:40:00-05:00",
    });
  });

  it("treats an expired halt as not halted, matching the nightly", async () => {
    vi.mocked(fetch).mockResolvedValue(
      fsResponse({
        halted: { booleanValue: true },
        reason: { stringValue: "stale" },
        expires_at: { stringValue: pastIso() },
      }),
    );
    expect(await readPipelineHalt()).toMatchObject({ halted: false });
  });

  it("treats a halt with no expiry as indefinitely live", async () => {
    vi.mocked(fetch).mockResolvedValue(
      fsResponse({
        halted: { booleanValue: true },
        reason: { stringValue: "legacy record" },
      }),
    );
    expect(await readPipelineHalt()).toMatchObject({
      halted: true,
      haltScope: "model",
    });
  });
});

describe("ageInDays", () => {
  it("counts whole days back from today in Chicago", () => {
    const now = new Date("2026-09-13T12:00:00-05:00");
    expect(ageInDays("2026-09-13", now)).toBe(0);
    expect(ageInDays("2026-09-12", now)).toBe(1);
    expect(ageInDays("2026-09-06", now)).toBe(7);
  });
});

describe("getSystemHealth", () => {
  beforeEach(() => {
    q.mockReset();
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => vi.unstubAllGlobals());

  it("flags stale data when the window has not advanced", async () => {
    vi.mocked(fetch).mockResolvedValue(fsResponse(null, 404));
    q.mockResolvedValue([{ data_window_end: "2000-01-01" }]);
    const h = await getSystemHealth();
    expect(h.stale).toBe(true);
    expect(h.dataWindowEnd).toBe("2000-01-01");
  });

  it("is not stale when the model reaches today", async () => {
    vi.mocked(fetch).mockResolvedValue(fsResponse(null, 404));
    const today = new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/Chicago",
    }).format(new Date());
    q.mockResolvedValue([{ data_window_end: today }]);
    expect((await getSystemHealth()).stale).toBe(false);
  });

  it("reports unknown rather than throwing when the lookup fails", async () => {
    vi.mocked(fetch).mockRejectedValue(new Error("no ADC"));
    q.mockResolvedValue([{ data_window_end: "2026-09-13" }]);
    const h = await getSystemHealth();
    expect(h.error).toContain("no ADC");
    expect(h.halted).toBe(false);
  });
});
