// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { ACTIVE_BASES } from "@/lib/restock/parse";

// actions.ts is a "use server" module pulling in BQ/server-only code that
// can't load outside Next's build pipeline — stub it, this test never
// exercises the real submit path.
const submitRestockAction = vi.fn();
const replaceEstimatedRestockDateAction = vi.fn();
vi.mock("@/app/inventory/actions", () => ({
  submitRestockAction: (...args: unknown[]) => submitRestockAction(...args),
  replaceEstimatedRestockDateAction: (...args: unknown[]) => replaceEstimatedRestockDateAction(...args),
}));

const { RestockImportDrawer } = await import("@/components/drawers/RestockImportDrawer");

describe("RestockImportDrawer — download sample CSV", () => {
  let createObjectURLSpy: ReturnType<typeof vi.fn<(obj: Blob) => string>>;
  let revokeObjectURLSpy: ReturnType<typeof vi.fn<(url: string) => void>>;
  let clickSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    createObjectURLSpy = vi.fn(() => "blob:mock-url");
    revokeObjectURLSpy = vi.fn();
    URL.createObjectURL = createObjectURLSpy as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = revokeObjectURLSpy;
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    submitRestockAction.mockReset();
    replaceEstimatedRestockDateAction.mockReset();
  });

  afterEach(() => {
    clickSpy.mockRestore();
    cleanup();
  });

  it("renders for the default add-order action and downloads a sample Blob", async () => {
    let createdAnchor: HTMLAnchorElement | undefined;
    const realCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreateElement(tag);
      if (tag === "a") createdAnchor = el as HTMLAnchorElement;
      return el;
    });

    render(<RestockImportDrawer dates={["2026-07-12"]} estimatedDates={["2026-07-23"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Restock…" }));

    const downloadButton = await screen.findByRole("button", { name: "Download sample CSV" });
    fireEvent.click(downloadButton);

    expect(createObjectURLSpy).toHaveBeenCalledTimes(1);
    const blob = createObjectURLSpy.mock.calls[0][0] as Blob;
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe("text/csv");
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURLSpy).toHaveBeenCalledWith("blob:mock-url");
    expect(createdAnchor?.download).toBe("restock-sample.csv");

    const text = await blob.text();
    expect(text.split("\n")).toEqual(["base,quantity", ...ACTIVE_BASES.map((b) => `${b},0`)]);
  });
});

describe("RestockImportDrawer — replace estimated date", () => {
  beforeEach(() => {
    submitRestockAction.mockReset();
    replaceEstimatedRestockDateAction.mockReset();
    replaceEstimatedRestockDateAction.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
  });

  it("shows From/To fields and no sample CSV for replace-estimated", async () => {
    render(
      <RestockImportDrawer
        dates={["2026-07-16"]}
        estimatedDates={["2026-07-23"]}
        defaultAction="replace-estimated"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Restock…" }));

    expect(screen.queryByRole("button", { name: "Download sample CSV" })).not.toBeInTheDocument();
    expect(await screen.findByLabelText("New delivery date")).toBeInTheDocument();
    expect(screen.getByText("Current estimated date")).toBeInTheDocument();
  });

  it("submits replaceEstimatedRestockDateAction with from/to", async () => {
    render(
      <RestockImportDrawer
        dates={["2026-07-16"]}
        estimatedDates={["2026-07-23"]}
        defaultAction="replace-estimated"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Restock…" }));

    const toInput = await screen.findByLabelText("New delivery date");
    fireEvent.change(toInput, { target: { value: "2026-07-25" } });

    fireEvent.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() => {
      expect(replaceEstimatedRestockDateAction).toHaveBeenCalledWith("2026-07-23", "2026-07-25");
    });
    expect(submitRestockAction).not.toHaveBeenCalled();
  });
});
