// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { ACTIVE_BASES } from "@/lib/restock/parse";

const submitRestockAction = vi.fn();
const moveRestockDateAction = vi.fn();
const removeRestockDateAction = vi.fn();
vi.mock("@/app/inventory/actions", () => ({
  submitRestockAction: (...args: unknown[]) => submitRestockAction(...args),
  moveRestockDateAction: (...args: unknown[]) => moveRestockDateAction(...args),
  removeRestockDateAction: (...args: unknown[]) => removeRestockDateAction(...args),
  replaceEstimatedRestockDateAction: vi.fn(),
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

const { RestockImportDrawer } = await import("@/components/drawers/RestockImportDrawer");

const scheduled = [
  { delivery_date: "2026-08-17", has_actuals: true },
  { delivery_date: "2026-08-20", has_actuals: false },
];

const estimateByDate = {
  "2026-08-20": ACTIVE_BASES.map((item) => ({
    item,
    quantityTubs: item === "Açaí" ? 12 : item === "Mango" ? 8 : 0,
  })),
};

function resetMocks() {
  submitRestockAction.mockReset();
  moveRestockDateAction.mockReset();
  removeRestockDateAction.mockReset();
  submitRestockAction.mockResolvedValue({ ok: true });
  moveRestockDateAction.mockResolvedValue({ ok: true });
  removeRestockDateAction.mockResolvedValue({ ok: true });
}

describe("RestockImportDrawer — quick actuals form", () => {
  beforeEach(() => {
    resetMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows estimate-prefilled qty inputs without requiring a file", async () => {
    render(
      <RestockImportDrawer
        dates={["2026-08-20"]}
        scheduledDates={scheduled}
        estimateByDate={estimateByDate}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Restock…" }));

    expect(await screen.findByLabelText("Açaí tubs")).toHaveValue(12);
    expect(screen.getByLabelText("Mango tubs")).toHaveValue(8);
    expect(screen.queryByRole("button", { name: "Download sample CSV" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Import CSV / photo…" }));
    expect(await screen.findByRole("button", { name: "Download sample CSV" })).toBeInTheDocument();
  });

  it("submits add-order with edited form rows", async () => {
    render(
      <RestockImportDrawer
        dates={["2026-08-20"]}
        scheduledDates={scheduled}
        estimateByDate={estimateByDate}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Restock…" }));

    const acai = await screen.findByLabelText("Açaí tubs");
    fireEvent.change(acai, { target: { value: "14" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() => {
      expect(submitRestockAction).toHaveBeenCalled();
    });
    const [date, action, rows] = submitRestockAction.mock.calls[0];
    expect(date).toBe("2026-08-20");
    expect(action).toBe("add-order");
    expect(rows.find((r: { item: string }) => r.item === "Açaí")?.quantityTubs).toBe(14);
  });
});

describe("RestockImportDrawer — sample CSV under import", () => {
  let createObjectURLSpy: ReturnType<typeof vi.fn<(obj: Blob) => string>>;
  let revokeObjectURLSpy: ReturnType<typeof vi.fn<(url: string) => void>>;
  let clickSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    createObjectURLSpy = vi.fn(() => "blob:mock-url");
    revokeObjectURLSpy = vi.fn();
    URL.createObjectURL = createObjectURLSpy as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = revokeObjectURLSpy;
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  afterEach(() => {
    clickSpy.mockRestore();
    cleanup();
  });

  it("downloads sample CSV from the optional import section", async () => {
    let createdAnchor: HTMLAnchorElement | undefined;
    const realCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreateElement(tag);
      if (tag === "a") createdAnchor = el as HTMLAnchorElement;
      return el;
    });

    render(<RestockImportDrawer dates={["2026-08-20"]} scheduledDates={scheduled} />);
    fireEvent.click(screen.getByRole("button", { name: "Restock…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Import CSV / photo…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Download sample CSV" }));

    expect(createObjectURLSpy).toHaveBeenCalledTimes(1);
    const blob = createObjectURLSpy.mock.calls[0][0] as Blob;
    expect(blob.type).toBe("text/csv");
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURLSpy).toHaveBeenCalledWith("blob:mock-url");
    expect(createdAnchor?.download).toBe("restock-sample.csv");
    const text = await blob.text();
    expect(text.split("\n")).toEqual(["base,quantity", ...ACTIVE_BASES.map((b) => `${b},0`)]);
  });
});

describe("RestockImportDrawer — move date", () => {
  beforeEach(() => {
    resetMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("submits moveRestockDateAction with from/to", async () => {
    render(
      <RestockImportDrawer
        dates={["2026-08-17"]}
        scheduledDates={scheduled}
        defaultAction="move-date"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Restock…" }));

    const toInput = await screen.findByLabelText("New delivery date");
    fireEvent.change(toInput, { target: { value: "2026-08-20" } });
    fireEvent.click(screen.getByRole("button", { name: "Move" }));

    await waitFor(() => {
      expect(moveRestockDateAction).toHaveBeenCalledWith("2026-08-17", "2026-08-20");
    });
    expect(submitRestockAction).not.toHaveBeenCalled();
  });
});

describe("RestockImportDrawer — remove date", () => {
  beforeEach(() => {
    resetMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("requires confirm before remove", async () => {
    render(
      <RestockImportDrawer
        dates={["2026-08-17"]}
        scheduledDates={scheduled}
        defaultAction="remove-date"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Restock…" }));

    const removeBtn = await screen.findByRole("button", { name: "Remove" });
    expect(removeBtn).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox"));
    expect(removeBtn).not.toBeDisabled();
    fireEvent.click(removeBtn);

    await waitFor(() => {
      expect(removeRestockDateAction).toHaveBeenCalledWith("2026-08-17");
    });
  });
});
