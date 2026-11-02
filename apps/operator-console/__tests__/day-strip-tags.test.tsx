// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { DayStripTags } from "@/components/labor/DayStripTags";
import type { DraftShift } from "@/lib/labor/shift-draft";

const shift = (employee: string | null, hours: number, extra: Partial<DraftShift> = {}): DraftShift => ({
  date: "2026-11-12", kind: "mid", startMin: 600, endMin: 600 + hours * 60, hours, employee, trimmed: false, ...extra,
});

afterEach(cleanup);

describe("DayStripTags", () => {
  it("reads a cleared week's unassigned drafts as open, assigned ones as draft, and hides short", () => {
    render(<DayStripTags open={1} drafts={[shift(null, 6), shift(null, 4), shift("Ann", 7)]} shortHours={5} />);
    expect(screen.getByText("+3 open")).toBeInTheDocument();
    expect(screen.getByText("+1 draft · 7h")).toBeInTheDocument();
    expect(screen.queryByText(/short/)).toBeNull();
  });

  it("ignores ADP-backed suggestions and still shows short when nothing is drafted", () => {
    render(<DayStripTags open={0} drafts={[shift("Ann", 7, { fillsOpen: true }), shift("Bo", 6, { inAdp: true })]} shortHours={2.5} />);
    expect(screen.queryByText(/open/)).toBeNull();
    expect(screen.queryByText(/draft/)).toBeNull();
    expect(screen.getByText("2.5h short")).toBeInTheDocument();
  });
});
