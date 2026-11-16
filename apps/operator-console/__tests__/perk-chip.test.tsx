// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("@/app/payroll/actions", () => ({
  endEmployeePerkAction: vi.fn().mockResolvedValue({ ok: true, message: "Perk stopped from this period on." }),
}));
vi.mock("@/lib/actions/ActionToast", () => ({ useActionToast: () => ({ push: vi.fn() }) }));
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PerkChip } from "@/components/payroll/PerkChip";
import { endEmployeePerkAction } from "@/app/payroll/actions";
import type { PeriodPerkRow } from "@/lib/bq/queries";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const MANAGE = { periodStart: "2026-11-02", periodLabel: "Nov 2 – Nov 15" };
const GYM: PeriodPerkRow = {
  employee: "Denton, Cimino R",
  perk_id: "gym",
  pay_period: "",
  amount_cents: 2000,
  note: null,
  updated_by: "adi@mypalmetto.co",
  terminated: true,
};

describe("PerkChip", () => {
  it("opens the perk sheet and only stops it after the confirm", async () => {
    render(<PerkChip perk={GYM} manage={MANAGE} showAmount={false} />);
    fireEvent.click(screen.getByRole("button", { name: /Gym for Denton, Cimino R/ }));
    expect(screen.getByText(/Terminated in ADP/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Stop perk…" }));
    expect(endEmployeePerkAction).not.toHaveBeenCalled();
    expect(screen.getByText(/Stops from Nov 2 – Nov 15 on/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("confirm-end-perk"));
    await waitFor(() =>
      expect(endEmployeePerkAction).toHaveBeenCalledWith("Denton, Cimino R", "gym", "", "2026-11-02"),
    );
  });

  it("Keep backs out without writing", () => {
    render(
      <PerkChip
        perk={{ ...GYM, perk_id: "mileage", pay_period: "2026-11-02..2026-11-15", terminated: false }}
        manage={MANAGE}
        showAmount
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Mileage for Denton/ }));
    fireEvent.click(screen.getByRole("button", { name: "Remove reimbursement…" }));
    expect(screen.getByText(/Comes off Nov 2 – Nov 15/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(screen.getByRole("button", { name: "Remove reimbursement…" })).toBeInTheDocument();
    expect(endEmployeePerkAction).not.toHaveBeenCalled();
  });
});
