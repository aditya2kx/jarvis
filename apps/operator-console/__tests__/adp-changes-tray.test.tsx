// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { AdpChange } from "@/lib/labor/adp-changes";

const apply = vi.fn();
const status = vi.fn();
const refresh = vi.fn();
vi.mock("@/app/labor/actions", () => ({
  applyAdpChangesAction: (c: unknown) => apply(c),
  adpChangesStatusAction: (id: string | null) => status(id),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
vi.mock("@/lib/actions/ActionToast", () => ({ useActionToast: () => ({ push: vi.fn() }) }));

import { AdpChangesTray } from "@/components/labor/AdpChangesTray";

beforeEach(() => {
  window.localStorage.clear();
  status.mockResolvedValue({ ok: true, data: { rows: [], running: null } });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

const T = { startMin: 420, endMin: 780 };
const SHIFT: AdpChange = { area: "shift", c: { kind: "shift-delete", date: "2026-10-31", employee: "Lee, Ana", ...T } };
const ASSIGN: AdpChange = { area: "shift", c: { kind: "open-assign", slot: "2026-11-01|adp|420|780", date: "2026-11-01", employee: "Bo, Kim", ...T } };
const UNAVAIL: AdpChange = { area: "unavailability", c: { kind: "approve", rowKey: "r1", employee: "Cy, Dee", summary: "Cy, Dee · Mon" } };

function tray(props: Partial<Parameters<typeof AdpChangesTray>[0]> = {}) {
  const p = { onUndo: vi.fn(), onDiscard: vi.fn(), onApplied: vi.fn(), onDismissNotice: vi.fn() };
  render(
    <AdpChangesTray
      store="palmetto"
      changes={[SHIFT, ASSIGN, UNAVAIL]}
      blocks={{ shift: null, unavailability: null }}
      {...p}
      {...props}
    />,
  );
  return p;
}

describe("AdpChangesTray", () => {
  it("one tray for shifts and unavailability, grouped on review, undo per line", () => {
    const p = tray();
    expect(screen.getByTestId("adp-changes-tray").textContent).toMatch(/3 changes to ADP · 3 people/);
    fireEvent.click(screen.getByRole("button", { name: /3 changes to ADP/ }));
    expect(screen.getByText("Shifts")).toBeTruthy();
    expect(screen.getByText("Unavailability")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Undo approve/ }));
    expect(p.onUndo).toHaveBeenCalledWith(["u:req|r1"]);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(p.onDiscard).toHaveBeenCalled();
  });

  it("blocks Apply per area: unavailability off blocks only when an unavailability change is staged", () => {
    tray({ blocks: { shift: null, unavailability: "off" } });
    expect((screen.getByRole("button", { name: "Apply to ADP" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/CONSOLE_ADP_UNAVAIL_WRITE/)).toBeTruthy();
    cleanup();
    tray({ changes: [SHIFT], blocks: { shift: null, unavailability: "off" } });
    expect((screen.getByRole("button", { name: "Apply to ADP" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("says read-only in preview", () => {
    tray({ blocks: { shift: "preview", unavailability: "preview" } });
    expect(screen.getByText(/Read-only preview/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Apply to ADP" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("confirms in a drawer, applies in one run, hands back what ADP confirmed and reloads", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const p = tray();
    apply.mockResolvedValue({ ok: true, data: { batchId: "b1", ids: ["c1", "c2", "c3"] }, queued: ["adp-schedule-write"] });
    fireEvent.click(screen.getByRole("button", { name: "Apply to ADP" }));
    expect(await screen.findByText("Apply 3 changes to ADP")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Apply 3 to ADP" }));
    await waitFor(() => expect(apply).toHaveBeenCalledWith([SHIFT, ASSIGN, UNAVAIL]));
    expect(JSON.parse(window.localStorage.getItem("labor-adp-apply:v1:palmetto")!).batchId).toBe("b1");

    status.mockResolvedValue({
      ok: true,
      data: {
        rows: [
          { change_id: "c1", area: "shift", summary: "Lee", status: "applied", error: null },
          { change_id: "c2", area: "shift", summary: "Open shift", status: "skipped", error: null },
          { change_id: "c3", area: "unavailability", summary: "Cy", status: "failed", error: "request gone" },
        ],
        running: null,
      },
    });
    await act(() => vi.advanceTimersByTimeAsync(5000));
    await waitFor(() =>
      expect(p.onApplied).toHaveBeenCalledWith(["s:shift|Lee, Ana|2026-10-31|420-780", "s:open|2026-11-01|adp|420|780"]),
    );
    expect(refresh).toHaveBeenCalled();
    expect(screen.getByText("2 done in ADP")).toBeTruthy();
    expect(screen.getByText("Cy: request gone")).toBeTruthy();
    expect(window.localStorage.getItem("labor-adp-apply:v1:palmetto")).toBeNull();
  });

  it("marks failed lines with a plain reason, keeps them across a reload, and retries only those", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    tray();
    apply.mockResolvedValue({ ok: true, data: { batchId: "b1", ids: ["c1", "c2", "c3"] }, queued: ["adp-schedule-write"] });
    fireEvent.click(screen.getByRole("button", { name: "Apply to ADP" }));
    fireEvent.click(await screen.findByRole("button", { name: "Apply 3 to ADP" }));
    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
    status.mockResolvedValue({
      ok: true,
      data: {
        rows: [
          { change_id: "c1", area: "shift", summary: "Lee", status: "failed",
            error: "ScheduleWriteError: details pane is not Lee, Ana 2026-10-31 7:00 AM-1:00 PM: 'Delete all schedules' (after one retry)" },
          { change_id: "c2", area: "shift", summary: "Open shift", status: "applied", error: null },
          { change_id: "c3", area: "unavailability", summary: "Cy", status: "applied", error: null },
        ],
        running: null,
      },
    });
    await act(() => vi.advanceTimersByTimeAsync(5000));
    await waitFor(() => expect(screen.getByText(/1 not done/)).toBeTruthy());
    expect(screen.getByTestId("apply-errors").textContent).toBe(
      "Lee: ADP wouldn't open this shift's details, so nothing was changed (tried twice)",
    );
    cleanup();

    // The page reloads with the failed line still in the tray.
    tray({ changes: [SHIFT] });
    await waitFor(() => expect(screen.getByText("1 failed last Apply")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /1 change to ADP/ }));
    expect(screen.getByText(/Failed last Apply: ADP wouldn't open/)).toBeTruthy();
    apply.mockResolvedValue({ ok: true, data: { batchId: "b2", ids: ["c9"] }, queued: ["adp-schedule-write"] });
    fireEvent.click(screen.getByRole("button", { name: "Retry 1 failed" }));
    await waitFor(() => expect(apply).toHaveBeenLastCalledWith([SHIFT]));
    expect(window.localStorage.getItem("labor-adp-failed:v1:palmetto")).toBeNull();
  });

  it("a reload resumes the Apply still running", async () => {
    status.mockResolvedValue({ ok: true, data: { rows: [], running: "b9" } });
    tray({ changes: [] });
    await waitFor(() => expect(screen.getByText(/Applying in ADP/)).toBeTruthy());
  });

  it("shows why changes were dropped, and is hidden with nothing to show", () => {
    tray({ changes: [], notice: "1 of your changes no longer match ADP" });
    expect(screen.getByText(/no longer match ADP/)).toBeTruthy();
    cleanup();
    tray({ changes: [] });
    expect(screen.queryByTestId("adp-changes-tray")).toBeNull();
  });
});
