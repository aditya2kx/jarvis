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
    { start: "2026-11-16", end: "2026-11-29" },
    { start: "2026-11-02", end: "2026-11-15" },
  ]),
}));

import { mutate } from "@/lib/bq/client";
import { addEmployeePerk, endEmployeePerk } from "@/lib/bq/writes";

describe("endEmployeePerk (migration 098 soft-end)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("soft-ends a recurring perk from an unpaid period, matching NULL or '' keys", async () => {
    await endEmployeePerk("palmetto", "Denton, Cimino R", "gym", "", "2026-11-02", "op@example.com");
    expect(mutate).toHaveBeenCalledTimes(1);
    const [sql, params, types] = vi.mocked(mutate).mock.calls[0];
    expect(sql).toMatch(/^UPDATE bhaga\.employee_perks/);
    expect(sql).not.toMatch(/DELETE/);
    expect(sql).toContain("IFNULL(pay_period, '') = IFNULL(@pay_period, '')");
    expect(sql).toContain("end_period_start IS NULL OR end_period_start > @from");
    expect(params).toMatchObject({ employee: "Denton, Cimino R", perk_id: "gym", pay_period: "", from: "2026-11-02" });
    expect(types).toEqual({ pay_period: "STRING" });
  });

  it("refuses a paid (or unknown) period", async () => {
    await expect(
      endEmployeePerk("palmetto", "Krause, Lindsay", "gym", "", "2026-10-19", "op@example.com"),
    ).rejects.toThrow(/only from an unpaid pay period/);
    expect(mutate).not.toHaveBeenCalled();
  });

  it("ends a one-off only on its own period", async () => {
    await expect(
      endEmployeePerk("palmetto", "Ortiz, Ximena", "mileage", "2026-11-02..2026-11-15", "2026-11-16", "op@example.com"),
    ).rejects.toThrow(/belongs to 2026-11-02\.\.2026-11-15/);
    await endEmployeePerk("palmetto", "Ortiz, Ximena", "mileage", "2026-11-02..2026-11-15", "2026-11-02", "op@example.com");
    expect(mutate).toHaveBeenCalledTimes(1);
  });

  it("re-adding a perk clears its end", async () => {
    await addEmployeePerk("palmetto", "Krause, Lindsay", "gym", 2000, "", "biweekly", "Misc reimbursement", "op@example.com");
    expect(vi.mocked(mutate).mock.calls[0][0]).toContain("end_period_start = NULL, ended_at = NULL, ended_by = NULL");
  });
});
