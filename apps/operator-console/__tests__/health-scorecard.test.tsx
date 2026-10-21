// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { HealthMetric, HealthScorecard as Data } from "@/lib/kpi/health";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("@/app/home/actions", () => ({ saveGoalAction: vi.fn() }));

const { HealthScorecard } = await import("@/components/kpi/HealthScorecard");

afterEach(cleanup);

function m(partial: Partial<HealthMetric> & Pick<HealthMetric, "key" | "label" | "status">): HealthMetric {
  return {
    actual: 1,
    goal: null,
    pace: null,
    formatted: "$1",
    goalFormatted: "—",
    goalKey: null,
    rawGoal: undefined,
    info: "info",
    ...partial,
  };
}

const data: Data = {
  win: { preset: "this_week", start: "2026-10-19", end: "2026-10-25", label: "This week" } as Data["win"],
  windowLabel: "This week",
  overallStatus: "off-track",
  metrics: [],
  groups: [
    {
      id: "top_line",
      label: "Sales (Square)",
      href: "/sales",
      metrics: [
        m({ key: "net_sales", label: "Net sales (Square)", status: "off-track", pace: 0.4, formatted: "$30,140", deltaFormatted: "$44.7K below goal · 40% of goal" }),
        m({ key: "orders", label: "Avg orders / day", status: "on-track", pace: 1.1 }),
      ],
    },
    {
      id: "cost",
      label: "Cost (bank)",
      href: "/accounting",
      metrics: [
        m({ key: "total_cost", label: "Total cost (bank)", status: "at-risk", pace: 0.9 }),
        m({ key: "cost_cat_rent", label: "Rent", status: "no-goal", detail: "12.0% of sales" }),
      ],
    },
  ],
};

describe("Home health scorecard", () => {
  it("leads with the verdict and lists what needs attention, worst first", () => {
    render(<HealthScorecard data={data} />);
    const summary = screen.getByTestId("health-summary");
    expect(within(summary).getByRole("heading")).toHaveTextContent("Needs attention");
    expect(within(summary).getByText("2 of 3 goals need attention")).toBeInTheDocument();
    const links = within(summary).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual([
      expect.stringContaining("Net sales (Square)"),
      expect.stringContaining("Total cost (bank)"),
    ]);
    expect(links[0]).toHaveAttribute("href", expect.stringContaining("/sales"));
  });

  it("untracked metrics show a value and detail without goal noise", () => {
    render(<HealthScorecard data={data} />);
    const rent = screen.getByTestId("metric-cost_cat_rent");
    expect(rent).toHaveTextContent("12.0% of sales");
    expect(rent).not.toHaveTextContent(/goal/i);
    expect(screen.queryByText("No goal set")).not.toBeInTheDocument();
  });
});
