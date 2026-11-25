import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ShopCodeCard } from "./ShopCodeCard";

const shopCodes = vi.fn();
const setShopCode = vi.fn();

vi.mock("./api", () => ({
  api: {
    shopCodes: (...a: unknown[]) => shopCodes(...a),
    setShopCode: (...a: unknown[]) => setShopCode(...a),
  },
  device: { set: vi.fn(), get: () => null },
}));

describe("ShopCodeCard", () => {
  beforeEach(() => {
    shopCodes.mockReset();
    setShopCode.mockReset();
    shopCodes.mockResolvedValue({
      url: "https://store-tasks.example/",
      stores: [
        {
          store: "palmetto",
          name: "Austin",
          set: true,
          code: "167260",
          needs_rotate: false,
          updated_at: "2026-11-25T14:55:33.283092+00:00",
        },
        {
          store: "houston",
          name: "Houston",
          set: true,
          code: null,
          needs_rotate: true,
          updated_at: "2026-11-01T00:00:00+00:00",
        },
      ],
    });
  });

  it("renders shown + needs_rotate rows with Copy and Issue controls", async () => {
    render(<ShopCodeCard token="sess" />);

    await waitFor(() => expect(screen.getByText("Austin")).toBeTruthy());
    expect(screen.getByText("Houston")).toBeTruthy();
    expect(screen.getByText("167260")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
    expect(
      screen.getByText(/Code is set but the digits weren’t stored/i),
    ).toBeTruthy();
    // Both rows are set → each shows "Issue new code" (blank = rotate/re-issue).
    expect(screen.getAllByRole("button", { name: "Issue new code" })).toHaveLength(2);
  });
});
