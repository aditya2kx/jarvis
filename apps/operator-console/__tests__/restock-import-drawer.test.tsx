// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { ACTIVE_BASES } from "@/lib/restock/parse";

// actions.ts is a "use server" module pulling in BQ/server-only code that
// can't load outside Next's build pipeline — stub it, this test never
// exercises the submit path.
vi.mock("@/app/inventory/actions", () => ({ submitRestockAction: vi.fn() }));

const { RestockImportDrawer } = await import("@/components/drawers/RestockImportDrawer");

// The drawer never writes to BQ on this path — this only exercises the
// client-side sample-CSV download, not submitRestockAction.
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

    render(<RestockImportDrawer dates={["2026-07-12"]} />);
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
