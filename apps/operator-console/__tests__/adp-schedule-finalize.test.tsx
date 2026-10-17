// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { pushRowKey, type PushRow, type PushShift } from "@/lib/labor/schedule-push";

const status = vi.fn();
vi.mock("@/app/labor/actions", () => ({
  schedulePushStatusAction: (w: string) => status(w),
  saveDraftsToAdpAction: vi.fn(),
  publishWeekAction: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("@/lib/actions/ActionToast", () => ({ useActionToast: () => ({ push: vi.fn() }) }));

import { AdpScheduleFinalize } from "@/components/labor/AdpScheduleFinalize";

const WEEK = "2026-10-26";
const SAVED = { date: "2026-10-26", employee: "Doe, Alex", startMin: 390, endMin: 930 };
const LEFT = { date: "2026-10-28", employee: "Roe, Sam", startMin: 510, endMin: 930 };
const UNSURE = { date: "2026-10-27", employee: null, startMin: 990, endMin: 1230 };

function row(s: PushShift, status: PushRow["status"], error: string | null = null): PushRow {
  return {
    push_id: "8c3f",
    row_key: pushRowKey("palmetto", s),
    date: s.date,
    employee: s.employee,
    start_min: s.startMin,
    end_min: s.endMin,
    status,
    error,
    updated_at: "2026-10-16 21:43:52.000000+00",
  };
}

function setup(rows: PushRow[], shifts: (PushShift & { inAdp?: boolean })[], scheduleReadAt: string | null) {
  status.mockResolvedValue({ ok: true, data: { rows, running: null } });
  render(<AdpScheduleFinalize weekStart={WEEK} shifts={shifts} enabled scheduleReadAt={scheduleReadAt} />);
  return screen.getByRole("button", { name: /Review & save as ADP drafts/ });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AdpScheduleFinalize after a save that stopped part-way (Issue #401)", () => {
  it("re-enables Save for shifts the stopped job left 'queued'", async () => {
    const btn = setup([row(SAVED, "drafted"), row(LEFT, "queued")], [SAVED, LEFT], "2026-10-16 21:51:18+00");
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByText(/save stopped with 1 shift not saved\. Save again to retry them/i)).toBeTruthy();
  });

  it("holds an unconfirmed shift until ADP's schedule is re-read, but still offers the rest", async () => {
    const rows = [
      row(SAVED, "drafted"),
      row(UNSURE, "failed", "UnconfirmedSave: ADP did not confirm (recheck failed)"),
      row(LEFT, "failed", "not attempted: stopped after an unconfirmed save"),
    ];
    const btn = setup(rows, [SAVED, UNSURE, LEFT], "2026-10-16 21:30:00+00");
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByTestId("adp-finalize-checking").textContent).toMatch(/1 shift may already be in ADP/);
    expect(screen.getByTestId("adp-finalize-status").textContent).toMatch(/1 shifts/);
  });

  it("drops the unconfirmed shift once ADP's re-read schedule shows it", async () => {
    const rows = [row(UNSURE, "failed", "UnconfirmedSave: x"), row(LEFT, "failed", "not attempted")];
    setup(rows, [{ ...UNSURE, inAdp: true }, LEFT], "2026-10-16 21:51:18+00");
    await waitFor(() => expect(screen.getByTestId("adp-finalize-status").textContent).toMatch(/1 shifts/));
    expect(screen.queryByTestId("adp-finalize-checking")).toBeNull();
  });
});
