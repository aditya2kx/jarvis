// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

vi.mock("@/app/inventory/actions", () => ({
  submitRestockAction: vi.fn(),
  applyOrderTubOverridesAction: vi.fn(),
  applyCurrentQtyOverridesAction: vi.fn(),
  clearCurrentQtyOverridesAction: vi.fn(),
  orderRecoStatusAction: vi.fn(),
}));

vi.mock("@/lib/inventory/useOrderRecoRefreshFollowup", () => ({
  useOrderRecoRefreshFollowup: () => ({ banner: null, followOrderReco: vi.fn() }),
}));

vi.mock("@/lib/actions/ActionToast", () => ({
  useActionToast: () => ({ push: vi.fn() }),
}));

const { OrderRecoTable } = await import("@/components/inventory/OrderRecoTable");

const rows = [
  { Item: "Açaí", "Current Qty": 12, "Avg per day": 2, "Days left": 6 },
  { Item: "Mango", "Current Qty": 8, "Avg per day": 1, "Days left": 8 },
];

function rowFor(item: string): HTMLElement {
  return screen.getAllByRole("row").find((r) => within(r).queryByText(item))!;
}

describe("OrderRecoTable — Manual Current Qty badge (Issue #438)", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
  });
  afterEach(() => cleanup());

  for (const writable of [true, false]) {
    it(`marks only overridden bases (writable=${writable})`, () => {
      render(
        <OrderRecoTable
          dates={[]}
          estimatedDates={[]}
          rows={rows}
          manualQtyItems={["Mango"]}
          writable={writable}
        />,
      );
      expect(within(rowFor("Mango")).getByText("Manual")).toHaveAttribute(
        "title",
        "Manual count — replaced by the next ClickUp closing",
      );
      expect(within(rowFor("Açaí")).queryByText("Manual")).not.toBeInTheDocument();
    });
  }

  it("shows no badge when nothing is overridden", () => {
    render(<OrderRecoTable dates={[]} estimatedDates={[]} rows={rows} writable />);
    expect(screen.queryByText("Manual")).not.toBeInTheDocument();
  });
});
