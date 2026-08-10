// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";

const submitRestockAction = vi.fn();
const applyOrderTubOverridesAction = vi.fn();

vi.mock("@/app/inventory/actions", () => ({
  submitRestockAction: (...args: unknown[]) => submitRestockAction(...args),
  applyOrderTubOverridesAction: (...args: unknown[]) => applyOrderTubOverridesAction(...args),
  pollOrderRecoRefreshAction: vi.fn(),
}));

vi.mock("@/lib/inventory/useOrderRecoRefreshFollowup", () => ({
  useOrderRecoRefreshFollowup: () => ({
    banner: null,
    followOrderReco: vi.fn(),
  }),
}));

vi.mock("@/lib/actions/ActionToast", () => ({
  useActionToast: () => ({ push: vi.fn() }),
}));

const { EstimateTubsDrawer } = await import("@/components/inventory/EstimateTubsDrawer");

describe("EstimateTubsDrawer — Actuals edit", () => {
  beforeEach(() => {
    submitRestockAction.mockReset();
    applyOrderTubOverridesAction.mockReset();
    submitRestockAction.mockResolvedValue({ ok: true });
  });

  afterEach(() => {
    cleanup();
  });

  it("saves Actuals via submitRestockAction add-order, not Manual overrides", async () => {
    render(
      <EstimateTubsDrawer
        open
        onOpenChange={() => {}}
        deliveryDate="2026-08-17"
        rows={[
          { item: "Açaí", orderTubs: 27, source: "Actuals" },
          { item: "Mango", orderTubs: 21, source: "Actuals" },
        ]}
      />,
    );

    expect(screen.getByText(/Edit actuals/)).toBeInTheDocument();
    expect(screen.queryByText("Mode")).not.toBeInTheDocument();

    const acai = screen.getByLabelText("Açaí tubs");
    fireEvent.change(acai, { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: /Apply/ }));

    await waitFor(() => {
      expect(submitRestockAction).toHaveBeenCalled();
    });
    const [date, action, rows] = submitRestockAction.mock.calls[0];
    expect(date).toBe("2026-08-17");
    expect(action).toBe("add-order");
    expect(rows.find((r: { item: string }) => r.item === "Açaí")?.quantityTubs).toBe(30);
    expect(applyOrderTubOverridesAction).not.toHaveBeenCalled();
  });
});
