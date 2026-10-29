// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PersonLaneTrack } from "@/components/labor/LaborCoveragePanel";
import type { CoveragePersonDay } from "@/lib/labor/coverage-model";
import type { UnavailabilityRow } from "@/lib/bq/queries";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const PERSON: CoveragePersonDay = {
  employee: "Lee, Ana",
  labor_bucket: "PT",
  segments: [
    { kind: "actual", startMin: 360, endMin: 420, hours: 1 },
    { kind: "scheduled", startMin: 420, endMin: 780, hours: 6 },
  ],
};
const OPEN: CoveragePersonDay = { employee: "Open shift", labor_bucket: "", segments: [{ kind: "open", startMin: 840, endMin: 1200, hours: 6 }] };
const ROW: UnavailabilityRow = {
  row_key: "adp|1",
  employee: "Lee, Ana",
  status: "approved",
  first_date: "2026-10-30",
  from_time: "18:00:00",
  to_time: "21:00:00",
  all_day: false,
  repeat_weekday: null,
  repeat_until: null,
  expires_at_ct: null,
  hours_left: null,
  scraped_at: null,
};

describe("PersonLaneTrack taps", () => {
  it("a scheduled or open shift is a button that opens it; clocked time is not", () => {
    const onShift = vi.fn();
    render(<PersonLaneTrack person={PERSON} axisStart={360} axisSpan={900} onShift={onShift} />);
    fireEvent.click(screen.getByRole("button", { name: "Lee, Ana · 7 AM–1 PM — change" }));
    expect(onShift).toHaveBeenCalledWith(PERSON.segments[1]);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    cleanup();
    const onOpen = vi.fn();
    render(<PersonLaneTrack person={OPEN} axisStart={360} axisSpan={900} onShift={onOpen} />);
    fireEvent.click(screen.getByRole("button", { name: "Open shift · 2 PM–8 PM — change" }));
    expect(onOpen).toHaveBeenCalledWith(OPEN.segments[0]);
  });

  it("a tap on a shift doesn't reach the lane, so the tooltip doesn't pin over the sheet", () => {
    const lane = vi.fn();
    render(
      <div onPointerDown={lane}>
        <PersonLaneTrack person={PERSON} axisStart={360} axisSpan={900} onShift={vi.fn()} />
      </div>,
    );
    fireEvent.pointerDown(screen.getByRole("button"));
    expect(lane).not.toHaveBeenCalled();
  });

  it("without onShift (today and earlier) nothing is a button", () => {
    render(<PersonLaneTrack person={PERSON} axisStart={360} axisSpan={900} blocks={[{ row: ROW, fromMin: 1080, toMin: 1260 }]} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByRole("img", { name: /Unavailable/ })).toBeTruthy();
  });

  it("an unavailability band opens its entry; a staged shift is ringed", () => {
    const onBlock = vi.fn();
    const { container } = render(
      <PersonLaneTrack
        person={PERSON}
        axisStart={360}
        axisSpan={900}
        blocks={[{ row: ROW, fromMin: 1080, toMin: 1260 }]}
        onShift={vi.fn()}
        onBlock={onBlock}
        staged={(seg) => seg.kind === "scheduled"}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Lee, Ana · Unavailable/ }));
    expect(onBlock).toHaveBeenCalledWith(ROW);
    expect(container.querySelector(".ring-primary")).toBeTruthy();
  });
});
