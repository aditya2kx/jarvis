// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ShiftChange } from "@/lib/labor/adp-changes";

const status = vi.fn();
const sendMessage = vi.fn();
vi.mock("@/app/labor/actions", () => ({
  schedulePushStatusAction: () => status(),
  sendPublishMessageAction: (w: string, to: string) => sendMessage(w, to),
}));
vi.mock("@/lib/actions/ActionToast", () => ({ useActionToast: () => ({ push: vi.fn() }) }));

import { AdpScheduleFinalize } from "@/components/labor/AdpScheduleFinalize";

const WEEK = "2026-10-26";
const T = { startMin: 420, endMin: 780 };
const ADD: ShiftChange = { kind: "shift-add", date: "2026-10-27", employee: "Doe, Alex", ...T };
const OPEN: ShiftChange = { kind: "open-add", date: "2026-10-27", count: 2, ...T };
const PUB: ShiftChange = { kind: "shift-publish", date: "2026-10-28", employee: "Roe, Sam", ...T };

function setup(changes: ShiftChange[], enabled = true) {
  status.mockResolvedValue({ ok: true, data: { running: null } });
  const onStage = vi.fn();
  render(<AdpScheduleFinalize weekStart={WEEK} changes={changes} onStage={onStage} enabled={enabled} />);
  return onStage;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AdpScheduleFinalize: the suggested week goes through the Changes to ADP tray (Issue #434)", () => {
  it("has no ADP write buttons of its own — only add-to-changes and the ClickUp message", () => {
    setup([ADD, OPEN, PUB]);
    const names = screen.getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual(["Review & add to changes", "Send ClickUp message"]);
    expect(screen.getByTestId("adp-finalize-status").textContent).toBe(
      "3 suggested shifts (18.0h, 2 open) aren't in ADP yet. 1 shift in ADP is still a draft staff can't see. Add them to your changes, then Apply to ADP.",
    );
  });

  it("stages every line after a review, and nothing before", async () => {
    const onStage = setup([ADD, OPEN, PUB]);
    fireEvent.click(screen.getByRole("button", { name: "Review & add to changes" }));
    expect(onStage).not.toHaveBeenCalled();
    expect(await screen.findByText(/Nothing changes in ADP until you press Apply to ADP/)).toBeTruthy();
    expect(screen.getByText("2 open shifts")).toBeTruthy();
    expect(screen.getByText("ADP draft")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add 3 lines to changes" }));
    expect(onStage).toHaveBeenCalledWith([ADD, OPEN, PUB]);
  });

  it("says so when everything is in ADP or staged, with nothing to add", () => {
    setup([]);
    expect(screen.getByTestId("adp-finalize-status").textContent).toMatch(/Everything suggested for this week is in ADP or in your changes/);
    expect((screen.getByRole("button", { name: "Review & add to changes" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("AdpScheduleFinalize Send ClickUp message", () => {
  it("opens a choice and can DM", async () => {
    sendMessage.mockResolvedValue({ ok: true, data: { weekStart: WEEK } });
    setup([]);
    fireEvent.click(screen.getByRole("button", { name: "Send ClickUp message" }));
    expect(sendMessage).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Send me a DM" }));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith(WEEK, "dm"));
  });

  it("can post to Shift Coverage", async () => {
    sendMessage.mockResolvedValue({ ok: true, data: { weekStart: WEEK } });
    setup([]);
    fireEvent.click(screen.getByRole("button", { name: "Send ClickUp message" }));
    fireEvent.click(await screen.findByRole("button", { name: "Post to Shift Coverage" }));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith(WEEK, "channel"));
  });

  it("is off while ADP schedule writes are off", () => {
    setup([ADD], false);
    expect((screen.getByRole("button", { name: "Send ClickUp message" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
