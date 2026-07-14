import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/lib/bq/client", () => ({
  mutate: vi.fn(async () => undefined),
  fq: (t: string) => `bhaga.${t}`,
  dateParam: (d: string) => d,
  intParam: (n: number) => n,
  q: vi.fn(async () => []),
}));

vi.mock("@/lib/bq/queries", () => ({
  unpaidPayPeriodWindows: vi.fn(async () => [
    { start: "2026-07-13", end: "2026-07-26" },
    { start: "2026-06-29", end: "2026-07-12" },
  ]),
}));

import { mutate } from "@/lib/bq/client";
import { unpaidPayPeriodWindows } from "@/lib/bq/queries";
import { applyTipExemptions } from "@/lib/bq/writes";

describe("applyTipExemptions unpaid-period guard (Issue #170)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(unpaidPayPeriodWindows).mockResolvedValue([
      { start: "2026-07-13", end: "2026-07-26" },
      { start: "2026-06-29", end: "2026-07-12" },
    ]);
  });

  it("rejects drafts outside every unpaid pay period", async () => {
    await expect(
      applyTipExemptions(
        "palmetto",
        [{ employeeName: "Alvarez, Sebastian", date: "2026-06-20", mode: "whole" }],
        "tester@example.com",
      ),
    ).rejects.toThrow(/editable only for unpaid pay periods/);
    expect(mutate).not.toHaveBeenCalled();
  });

  it("MERGEs a window draft in the just-ended unpaid period", async () => {
    await applyTipExemptions(
      "palmetto",
      [
        {
          employeeName: "Alvarez, Sebastian",
          date: "2026-07-10",
          mode: "window",
          exemptStart: "18:00",
          exemptEnd: "18:30",
          note: "Meeting",
        },
      ],
      "tester@example.com",
    );
    expect(mutate).toHaveBeenCalledTimes(1);
  });

  it("MERGEs a draft in the current in-progress unpaid period", async () => {
    await applyTipExemptions(
      "palmetto",
      [{ employeeName: "Alvarez, Sebastian", date: "2026-07-14", mode: "whole", note: "Training" }],
      "tester@example.com",
    );
    expect(mutate).toHaveBeenCalledTimes(1);
    // whole-day null start/end must declare STRING types for the Node BQ client
    expect(mutate).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ start: null, end: null, note: "Training" }),
      { start: "STRING", end: "STRING" },
    );
  });

  it("rejects inverted windows", async () => {
    await expect(
      applyTipExemptions(
        "palmetto",
        [
          {
            employeeName: "Alvarez, Sebastian",
            date: "2026-07-10",
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
