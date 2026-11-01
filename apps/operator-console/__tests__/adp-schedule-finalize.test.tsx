// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ShiftChange } from "@/lib/labor/adp-changes";

const status = vi.fn();
const sendMessage = vi.fn();
vi.mock("@/app/labor/actions", () => ({
  schedulePushStatusAction: () => status(),
  sendPublishMessageAction: (w: string[], to: string) => sendMessage(w, to),
}));
vi.mock("@/lib/actions/ActionToast", () => ({ useActionToast: () => ({ push: vi.fn() }) }));

import { AdpScheduleFinalize } from "@/components/labor/AdpScheduleFinalize";

const WEEK = "2026-10-26";
const T = { startMin: 420, endMin: 780 };
const ADD: ShiftChange = { kind: "shift-add", date: "2026-10-27", employee: "Doe, Alex", ...T };
const OPEN: ShiftChange = { kind: "open-add", date: "2026-10-27", count: 2, ...T };
const PUB: ShiftChange = { kind: "shift-publish", date: "2026-10-28", employee: "Roe, Sam", ...T };

const NEXT = "2026-11-02";
const LATER = "2026-11-09";
const NEXT_ADD: ShiftChange = { kind: "shift-add", date: "2026-11-03", employee: "Doe, Alex", ...T };
const LATER_ADD: ShiftChange = { kind: "shift-add", date: "2026-11-10", employee: "Roe, Sam", ...T };

function setup(changes: ShiftChange[], enabled = true, more: { weekStart: string; changes: ShiftChange[] }[] = []) {
  status.mockResolvedValue({ ok: true, data: { running: null } });
  const onStage = vi.fn();
  render(
    <AdpScheduleFinalize
      weekStart={WEEK}
      weeks={[{ weekStart: WEEK, changes }, ...more]}
      onStage={onStage}
      enabled={enabled}
    />,
  );
  return onStage;
}

const weekBox = (week: string) => within(screen.getByTestId(`week-${week}`)).getByRole("checkbox");

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
    sendMessage.mockResolvedValue({ ok: true, data: { weekStarts: [WEEK] } });
    setup([]);
    fireEvent.click(screen.getByRole("button", { name: "Send ClickUp message" }));
    expect(sendMessage).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Send me a draft" }));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith([WEEK], "dm"));
  });

  it("can post to Shift Coverage", async () => {
    sendMessage.mockResolvedValue({ ok: true, data: { weekStarts: [WEEK] } });
    setup([]);
    fireEvent.click(screen.getByRole("button", { name: "Send ClickUp message" }));
    fireEvent.click(await screen.findByRole("button", { name: "Post to Shift Coverage" }));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith([WEEK], "channel"));
  });

  it("is off while ADP schedule writes are off", () => {
    setup([ADD], false);
    expect((screen.getByRole("button", { name: "Send ClickUp message" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("AdpScheduleFinalize: several weeks in one go", () => {
  it("says other weeks have suggestions, and adds the picked weeks together", async () => {
    const onStage = setup([ADD], true, [
      { weekStart: NEXT, changes: [NEXT_ADD] },
      { weekStart: LATER, changes: [LATER_ADD] },
    ]);
    expect(screen.getByTestId("adp-finalize-status").textContent).toMatch(/2 other weeks have suggestions too\.$/);
    fireEvent.click(screen.getByRole("button", { name: "Review & add to changes" }));
    await screen.findByText("Suggested shifts");
    fireEvent.click(weekBox(NEXT));
    fireEvent.click(weekBox(LATER));
    fireEvent.click(screen.getByRole("button", { name: "Add 3 lines to changes · 3 weeks" }));
    expect(onStage).toHaveBeenCalledWith([ADD, NEXT_ADD, LATER_ADD]);
  });

  it("announces the weeks just added in one message", async () => {
    sendMessage.mockResolvedValue({ ok: true, data: { weekStarts: [WEEK, NEXT] } });
    setup([ADD], true, [{ weekStart: NEXT, changes: [NEXT_ADD] }, { weekStart: LATER, changes: [] }]);
    fireEvent.click(screen.getByRole("button", { name: "Review & add to changes" }));
    await screen.findByText("Suggested shifts");
    fireEvent.click(weekBox(NEXT));
    expect(weekBox(LATER).hasAttribute("data-disabled")).toBe(true);
    expect(within(screen.getByTestId(`week-${LATER}`)).getByText("In ADP or your changes")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add 2 lines to changes · 2 weeks" }));
    fireEvent.click(screen.getByRole("button", { name: "Send ClickUp message" }));
    expect(await screen.findByText("Covers Oct 26 – Nov 1 and Nov 2–8.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Post to Shift Coverage" }));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith([WEEK, NEXT], "channel"));
  });

  it("can't send a message with no week picked", async () => {
    setup([ADD]);
    fireEvent.click(screen.getByRole("button", { name: "Send ClickUp message" }));
    await screen.findByText("Send the schedule message");
    fireEvent.click(weekBox(WEEK));
    expect(screen.getByText("Pick at least one week.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Post to Shift Coverage" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("starts from the first week with suggestions when this week has none", async () => {
    const onStage = setup([], true, [{ weekStart: NEXT, changes: [NEXT_ADD] }]);
    fireEvent.click(screen.getByRole("button", { name: "Review & add to changes" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add 1 line to changes" }));
    expect(onStage).toHaveBeenCalledWith([NEXT_ADD]);
  });
});
