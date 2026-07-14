import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("server-only", () => ({}));

// Exercise open-period guard + window validation without hitting BQ.
vi.mock("@/lib/bq/client", () => ({
  mutate: vi.fn(async () => undefined),
  fq: (t: string) => `bhaga.${t}`,
  dateParam: (d: string) => d,
  intParam: (n: number) => n,
  q: vi.fn(async () => []),
}));

vi.mock("@/lib/bq/queries", () => ({
  openPayPeriodBounds: vi.fn(async () => ({ start: "2026-07-13", end: "2026-07-26" })),
}));

import { mutate } from "@/lib/bq/client";
import { openPayPeriodBounds } from "@/lib/bq/queries";
import { applyTipExemptions } from "@/lib/bq/writes";

describe("applyTipExemptions open-period guard (Issue #167)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(openPayPeriodBounds).mockResolvedValue({
      start: "2026-07-13",
      end: "2026-07-26",
    });
  });

  it("rejects drafts outside the open pay period", async () => {
    await expect(
      applyTipExemptions(
        "palmetto",
        [{ employeeName: "Alvarez, Sebastian", date: "2026-07-10", mode: "whole" }],
        "tester@example.com",
      ),
    ).rejects.toThrow(/editable only for the current open pay period/);
    expect(mutate).not.toHaveBeenCalled();
  });

  it("MERGEs a window draft inside the open period", async () => {
    await applyTipExemptions(
      "palmetto",
      [
        {
          employeeName: "Alvarez, Sebastian",
          date: "2026-07-13",
          mode: "window",
          exemptStart: "18:00",
          exemptEnd: "18:30",
          note: "Meeting",
        },
      ],
      "tester@example.com",
    );
    expect(mutate).toHaveBeenCalledTimes(1);
    const sql = String(vi.mocked(mutate).mock.calls[0][0]);
    expect(sql).toMatch(/MERGE/);
    expect(sql).toMatch(/exempt_start/);
  });

  it("rejects inverted windows", async () => {
    await expect(
      applyTipExemptions(
        "palmetto",
        [
          {
            employeeName: "Alvarez, Sebastian",
            date: "2026-07-13",
            mode: "window",
            exemptStart: "19:00",
            exemptEnd: "18:00",
          },
        ],
        "tester@example.com",
      ),
    ).rejects.toThrow(/exempt end must be after start/);
  });
});
