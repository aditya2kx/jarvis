import { describe, it, expect, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("@/lib/bhaga/health", async (orig) => {
  const actual = await orig<typeof import("@/lib/bhaga/health")>();
  return { ...actual, getSystemHealth: vi.fn() };
});

import { healthMessage } from "@/components/shell/HealthBanner";
import type { SystemHealth } from "@/lib/bhaga/health";

const HEALTHY: SystemHealth = {
  halted: false,
  haltReason: null,
  haltScope: null,
  haltSince: null,
  dataWindowEnd: "2026-09-13",
  dataAgeDays: 0,
  stale: false,
  error: null,
};

describe("healthMessage", () => {
  it("shows nothing when the system is healthy", () => {
    expect(healthMessage(HEALTHY)).toBeNull();
  });

  it("says raw ingest continues for a model-scoped halt", () => {
    const msg = healthMessage({
      ...HEALTHY,
      halted: true,
      haltScope: "model",
      haltReason: "tip pool NOT conserved",
      haltSince: "2026-09-07T21:40:00-05:00",
    });
    expect(msg?.tone).toBe("danger");
    expect(msg?.detail).toContain("raw data is still being collected");
    expect(msg?.detail).toContain("tip pool NOT conserved");
  });

  it("says the whole run is stopped for an all-scoped halt", () => {
    const msg = healthMessage({
      ...HEALTHY,
      halted: true,
      haltScope: "all",
      haltReason: "boom",
    });
    expect(msg?.detail).toContain("nightly run is stopped");
  });

  it("names the date the data stops at when stale", () => {
    const msg = healthMessage({
      ...HEALTHY,
      dataWindowEnd: "2026-09-06",
      dataAgeDays: 7,
      stale: true,
    });
    expect(msg?.tone).toBe("warn");
    expect(msg?.headline).toContain("7 days ago");
    expect(msg?.detail).toContain("2026-09-06");
  });

  it("admits when health could not be determined", () => {
    const msg = healthMessage({ ...HEALTHY, error: "no ADC" });
    expect(msg?.tone).toBe("muted");
    expect(msg?.headline).toContain("unknown");
  });

  it("prefers the halt over staleness — the halt explains the staleness", () => {
    const msg = healthMessage({
      ...HEALTHY,
      halted: true,
      haltScope: "model",
      stale: true,
      dataAgeDays: 6,
    });
    expect(msg?.headline).toBe("Pipeline halted");
  });
});
